from __future__ import unicode_literals

from time import sleep
import json
import os
import pathlib
import select
import socket

from digitalocean.baseapi import NotFoundError
from frappe.model.document import Document
from github import Github
from paramiko import (AuthenticationException, BadHostKeyException, SSHException)
import digitalocean
import frappe
import paramiko
import regex
from frappe.utils.password import get_decrypted_password


BENCH_COMMANDS = ("bench setup requirements", "bench build", "bench migrate", "bench restart")
DEFAULT_PARAMS = {'depends_on': None, 'droplet_size': 's-2vcpu-4gb'}
DROPLET_SIZE = {'1VCPU3GB': 's-1vcpu-3gb', '2VCPU4GB': 's-2vcpu-4gb', '4VCPU8GB': 's-4vcpu-8gb'}


class HaltExecution(Exception): pass

class DeployerInstance(Document):


	def initialize(self, parameters, repository_fullname, deployer_config):
		self.deployer_config = deployer_config
		self.repository_fullname = repository_fullname
		self.parameters = parameters
		self.initialize_instance()


	def get_repository(self):
		if not self.deployer_config:
			self.deployer_config = frappe.get_single("Deployer Config")
		g = Github(self.deployer_config.bot_username, get_decrypted_password("Deployer Config", self.deployer_config.name, "access_token"))
		self.repository = g.get_repo(self.repository_fullname)


	def get_pull_request(self):
		self.get_repository()
		self.pull_request = self.repository.get_pull(self.pull_request_number)


	def get_pull_branch(self):
		self.get_pull_request()
		self.pull_request_branch = self.pull_request.base.ref


	def set_pull_request_status(self, description, state, target_url=""):
		self.get_pull_request()
		self.repository.get_commit(sha=self.pull_request.head.sha).create_status(description=description,
			context="PR Bot",
			state=state,
			target_url=target_url)


	def get_manager(self):
		if not self.get("deployer_config"):
			self.deployer_config = frappe.get_single("Deployer Config")
		self.manager = digitalocean.Manager(token=get_decrypted_password("Deployer Config", self.deployer_config.name, "do_token"))


	def exec_command(self, cmd='ls', directory=None, bench_dir="/home/frappe/frappe-bench"):
		if not self.client:
			self.connect_ssh(user="frappe")
		if directory:
			directory = os.path.join(bench_dir, directory)
		else:
			directory = bench_dir

		stdin, stdout, stderr = self.client.exec_command("cd {dir}; {cmd}".format(dir=directory, cmd=cmd))

		log = str()
		while not stdout.channel.exit_status_ready():
			if stdout.channel.recv_ready():
				rl, wl, xl = select.select([stdout.channel], [], [], 0.0)
				if len(rl) > 0:
					log += str(stdout.channel.recv(1024))
					log += "\n"

		if stdout.channel.recv_exit_status() not in (0, -1):
			if not self.pull_request:
				self.get_pull_request()
			self.pull_request.create_issue_comment("An error occurred while executing the following command:\n\
					```{0}```\nYou may manually fix the issue using: `ssh {1} -l frappe`\n\
					Traceback:\n```{2}```".format(cmd, self.instance_url, log))
			self.set_pull_request_status("An error has occurred. Please check comments for more information.", "error")
			raise HaltExecution()


	def initialize_instance(self):
		self.set_pull_request_status("The instance is being prepared", "pending")
		self.get_pull_branch()
		if self.pull_request_branch not in tuple(branch.strip() for branch in self.deployer_config.branch_whitelist.split(',')):
			self.set_pull_request_status("Instances cannot be deployed for the current branch", "success")
			return
		existing_instance_url = frappe.get_value('Deployer Instance', {
			'pull_request_number': self.pull_request_number,
			'application_being_tested': self.application_being_tested,
			'is_active': True
		}, 'instance_url')
		if existing_instance_url:
			self.set_pull_request_status("The instance has been successfully deployed", "success",
				target_url="".join(["http://", existing_instance_url]))
			return
		if self.create_droplet():
			self.setup_instance()


	def create_droplet(self):
		if frappe.db.count("Deployer Instance") + 1 > self.deployer_config.max_instances:
			self.set_pull_request_status("Maximum number of concurrently active instances reached", "error")
			self.destroy_instance(error=True)
			return
		self.instance_name = '-'.join([self.application_being_tested, "PR", str(self.pull_request_number), self.instance_requested_by])
		self.get_manager()
		self.droplet = digitalocean.Droplet(token=get_decrypted_password("Deployer Config", self.deployer_config.name, "do_token"),
				name=self.instance_name,
				size=self.parameters.get('droplet_size'),
				region="blr1",
				ssh_keys=self.manager.get_all_sshkeys(),
				image=self.deployer_config.snapshot_id)
		self.droplet.create()
		while True:
			action = self.droplet.get_actions()[0]
			if (action.type, action.status) == ('create', 'completed'):
				break
			sleep(10)
		self.droplet = self.manager.get_droplet(self.droplet.id)
		self.instance_url = self.droplet.ip_address
		self.droplet_id = self.droplet.id
		self.is_active = True
		self.instance_created_at = frappe.utils.now()
		self.save(ignore_permissions=True)
		return True


	def setup_instance(self, update=False):
		if update:
			self.get_pull_branch()
		if self.connect_ssh(retries=6, user="frappe"):
			self.update_applications()
			self.fetch_dependent_pull_requests()

			for command in BENCH_COMMANDS:
				self.exec_command(cmd=command)

			self.set_pull_request_status("The instance has been deployed", "success",
				target_url="".join(["http://", self.instance_url]))
		else:
			self.set_pull_request_status("An error has occured while trying to SSH, please try again", "error")
			self.destroy_instance(error=True)


	def connect_ssh(self, retries=6, user="root"):
		self.client = paramiko.SSHClient()
		self.client.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())

		for _ in range(retries):
			try:
				self.client.connect(str(self.instance_url), username=user)
				return True
			except (BadHostKeyException, AuthenticationException, SSHException, socket.error) as e:
				sleep(10)
		return False


	def update_applications(self):
		COMMANDS = ("git reset HEAD --hard", "git checkout {}".format(self.pull_request_branch),
			"git pull upstream {}".format(self.pull_request_branch))
		for app in ('frappe', 'erpnext'):
			for command in COMMANDS:
				self.exec_command(cmd=command, directory="apps/{}".format(app))
			if app == self.application_being_tested:
				self.exec_command(cmd="git pull upstream pull/{}/head".format(self.pull_request_number),
					directory="apps/{}".format(app))


	def fetch_dependent_pull_requests(self):
		depends_on = self.parameters.get('depends_on', None)
		if depends_on and isinstance(depends_on, (list, str)):
			if isinstance(depends_on, str):
				depends_on = [depends_on]
			for dependency in depends_on:
				repo, pull_request = depends_on.split('#')
				if repo.casefold() in ('frappe', 'erpnext') and pull_request.is_digit():
					self.exec_command(cmd="git pull upstream pull/{}/head".format(pull_request), directory="apps/{}".format(repo))


	def update_instance(self, repository_fullname):
		self.repository_fullanem = repository_fullname
		self.set_pull_request_status("The instance is being updated", "pending")
		self.setup_instance(update=True)


	def destroy_instance(self, repo=None, error=False):
		if repo:
			self.repository_fullname = repo
		self.instance_destroyed_at = frappe.utils.now()
		self.is_active = False
		try:
			self.get_manager()
			self.droplet = self.manager.get_droplet(self.droplet_id)
			self.droplet.destroy()
			while True:
				action = self.droplet.get_actions()[0]
				if (action.type, action.status) == ('destroy', 'completed'):
					break
				sleep(5)
		except NotFoundError:
			pass
		self.save(ignore_permissions=True)
		if not error:
			self.set_pull_request_status("The instance has been destroyed", "success")



@frappe.whitelist(allow_guest=True)
def deploy(context):
	context = json.loads(context)
	if 'issue' in context and 'pull_request' in context.get('issue') and context.get('issue', {}).get('state') == 'open' and context.get('action') != "deleted":
		comment = context.get('comment', {}).get('body')
		user = context.get('comment', {}).get('user', {}).get('login')
		deployer_config = frappe.get_single("Deployer Config")
		allowed_requesters = deployer_config.allowed_requesters.split('\n')
		bot_username = deployer_config.bot_username

		if user in allowed_requesters:
			repository = context.get('repository', {}).get('name')
			repository_fullname = context.get('repository', {}).get('full_name')
			pull_request = context.get('issue', {}).get('number')
			if "@{} create instance".format(bot_username) in comment:
				if not frappe.db.exists("Deployer Instance", {
						'application_being_tested': repository,
						'pull_request_number': pull_request,
						'is_active': True
				}):
					parameters = get_additional_params(comment)
					repository_name = context.get('repository', {}).get('full_name')
					deployer_instance = frappe.get_doc({
						'doctype': 'Deployer Instance',
						'application_being_tested': repository,
						'instance_requested_by': user,
						'pull_request_number': pull_request
					})
					deployer_instance.is_active = True
					deployer_instance.save(ignore_permissions=True)
					frappe.enqueue_doc(deployer_instance.doctype, deployer_instance.name, "initialize", timeout=1000,
							repository_fullname=repository_fullname,
							parameters=parameters,
							deployer_config=deployer_config)
			elif "@{} destroy instance".format(bot_username) in comment:
				stop_instance(pull_request, repository_fullname)

	elif context.get('action') == "synchronize":
		try:
			deployer_instance = frappe.get_doc("Deployer Instance", {
				'application_being_tested': context.get('repository', {}).get('name'),
				'pull_request_number': context.get('number'),
				'is_active': True
			})
			frappe.enqueue_doc(deployer_instance.doctype, deployer_instance.name, "update_instance", timeout=1000,
					repository_fullname=context.get("repository", {}).get('full_name'))
			return
		except frappe.DoesNotExistError:
			pass
		try:
			deployer_instance = frappe.get_doc("Deployer Instance", {
				'depends_on': "#".join([context.get('repository', {}).get('name'), context.get('number')]),
				'is_active': True
			})
			frappe.enqueue_doc(deployer_instance.doctype, deployer_instance.name, "update_instance", timeout=1000,
					repository_fullname=context.get("repository", {}).get('full_name'))
		except frappe.DoesNotExistError:
			pass

	elif context.get('action') == "closed":
			stop_instance(context.get('number'), context.get('base', {}).get('repo', {}).get('name'))


def stop_instance(pull_request_number, repository_name):
	application_being_tested = repository_name.split('/')[1]
	active_droplets = frappe.get_all('Deployer Instance',
				filters={
					'application_being_tested': application_being_tested,
					'pull_request_number': pull_request_number,
					'is_active': True
				}, fields=['droplet_id'])
	if active_droplets:
		for droplet in active_droplets:
			deployer_instance = frappe.get_doc("Deployer Instance", {
				'droplet_id': droplet.get('droplet_id')
			})
			deployer_instance.destroy_instance(repo=repository_name)


def get_additional_params(comment):
	try:
		comment = regex.search(r"[\{\[](?:[^{}]|(?R))*[\}\]]", comment).group(0)
		parameters = json.loads(comment.replace('\'', '"'))
	except AttributeError:
		parameters = dict()

	if not parameters:
		return DEFAULT_PARAMS

	if isinstance(parameters, list):
		params = dict()
		try:
			params.update({
				'depends_on': parameters[0],
				'droplet_size': DROPLET_SIZE.get(parameters[1], 's-2vcpu-2gb')
			})
		except IndexError:
			if any(repo in parameters for repo in REPOSITORIES):
				params.update({
					'depends_on': parameters[0],
					'droplet_size': 's-2vcpu-2gb'
				})
			elif 'VCPU' in parameters:
				params.update({
					'depends_on': None,
					'droplet_size': parameters[0]
				})
		return params

	return parameters

