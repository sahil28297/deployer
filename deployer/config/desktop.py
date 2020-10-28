# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from frappe import _

def get_data():
	return [
		{
			"module_name": "Deployer",
			"color": "red",
			"icon": "octicon octicon-git-pull-request",
			"type": "module",
			"label": _("Deployer")
		}
	]
