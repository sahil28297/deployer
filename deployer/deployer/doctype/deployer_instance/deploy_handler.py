# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

from __future__ import unicode_literals
from deployer.deployer.doctype.deployer_instance.deployer_instance import deploy
from hashlib import sha1

import hmac
import json
import frappe


@frappe.whitelist(allow_guest=True)
def handle_event(*args, **kwargs):
	'''
	Set the webhook URL in GitHub to point to this method:
		https://[hostname]/api/method/deployer.deployer.doctype.deployer_instance.deploy_handler.handle_event
		content_type: application/json

	add the webhook secret to common_site_config.json as:
		{
			"deployer_secret": "your-secret-key"
		}
	ensure that the secret key is the same as the one provided to GitHub
	'''
	r = frappe.request

	try:
		authenticate_request(r)
		payload = json.dumps(json.loads(r.get_data()))
		d = deploy(payload)
		return '', 200
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), 'Deployer Instance')

	return '', 500


def authenticate_request(request):
	deployer_secret = frappe.conf.deployer_secret
	header_signature = request.headers.get('X-Hub-Signature')
	if header_signature is None:
		raise Exception

	sha_name, signature = header_signature.split('=')
	if sha_name !='sha1':
		raise Exception

	mac = hmac.new(deployer_secret.encode(), msg=request.data, digestmod='sha1')
	if not hmac.compare_digest(str(mac.hexdigest()), str(signature)):
		raise Exception
