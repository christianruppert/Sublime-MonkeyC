import sublime
import sublime_plugin

import subprocess
import threading
import os

import socket # for checking simulator tcp port
from MonkeyC.helpers.manifest import Manifest
from MonkeyC.helpers.sdk import SDK

noop = lambda *x, **y: None

"""
	TODO:
		- SDK Path management
		- change output panel syntax based on used command
		- ability to include extra jungle files (for apps and barrels)
		- build using non-jungle style (-w -z resources, etc)
		- build for device
			- device
			- release ver y/n
		- "IDE management"
			- project scaffolding/setup
				- see: SDK/bin/projectInfo.xml and templates/
			- manifest.xml management?
				- permissions
				- supported devices
				- min SDK ver
				- see: SDK/bin/projectInfo.xml
			- monkey.jungle setup?
"""


class MonkeyBuildCommand(sublime_plugin.WindowCommand):
	"""Builds ConnectIQ-based projects in sublime text"""

	def __init__(self, window):
		# for some reason this did NOT like the normal way
		sublime_plugin.WindowCommand.__init__(self,window)
		self.device_select = DeviceInput()

	"""
	encoding="utf-8"
	killed=False
	proc=None
	panel=None
	panel_lock=threading.Lock()
	"""

	def is_enabled(self, *args, **kwargs):
		"""Return true if the command can be CANCELLED or RUN at a given time"""
		if 'kill' in kwargs: # build system cancel check
			return False
		# being checked otherwise, e.g. through command palette
		if 'enabled' in kwargs:
			return kwargs['enabled']
		return True

	def is_visible(self, *args, **kwargs):
		if 'kill' in kwargs: # build system cancel check
			return False
		# being checked otherwise, e.g. through command palette
		return True

	def description(self, *args, **kwargs):
		"""Shown as caption in menu items when caption isn't present"""
		return "something something"

	def input(self, *args, **kwargs):
		# @todo: skip inputs if there is only one choice
		# e.g. only one supported device
		self.get_settings()
		self.device_select.set_sdk(self.sdk_path)
		self.device_select.set_work_dir(self.vars["folder"])
		return self.device_select


	def get_settings(self):
		self.settings = sublime.load_settings("MonkeyCBuild.sublime-settings")
		# @todo: override with project settings

		self.sdk_path = self.settings.get("sdk","")
		self.bin = os.path.expanduser(os.path.join(self.sdk_path,"bin"))
		self.key = os.path.expanduser(self.settings.get("key",""))

		project_data = self.window.project_data() # JSON from sublime project
		project_settings = (project_data or {}).get("monkeyc",{}) # {} should override setting with these

		view_settings = self.window.active_view().settings()
		self.vars = self.window.extract_variables()
		"""
		{
			'project': '/home/dan/dev/sub-projects/monkeyc.sublime-project',
			'project_extension': 'sublime-project',
			'file': '/home/dan/dev/Sublime-MonkeyC/monkey_build.py',
			'file_path': '/home/dan/dev/Sublime-MonkeyC',
			'platform': 'Linux',
			'folder': '/home/dan/dev/Sublime-MonkeyC',
			'packages': '/home/dan/.config/sublime-text-3/Packages',
			'file_name': 'monkey_build.py',
			'file_base_name': 'monkey_build',
			'project_name': 'monkeyc.sublime-project',
			'file_extension': 'py',
			'project_base_name': 'monkeyc',
			'project_path': '/home/dan/dev/sub-projects'
		}
		"""



	def run(self, *args, **kwargs):

		sublime.status_message("Building...")

		self.get_settings()

		self.panel = Panel(self.window)
		self.panel.print("[{}]",*args)
		self.panel.print("[{}]",str(kwargs))


		# apps compile with monkeyc, barrels(modules) with barrelbuild
		# so we need to know which we are dealing with
		self.build_for = self.detect_app_vs_barrel()
		self.compiler = Compiler(self.bin,self.vars["folder"],self.key)
		self.simulator = Simulator(self.bin)

		#if "device" in kwargs and kwargs["device"] == "prompt":
		#	self.window.show_quick_panel(["fenix5","fr935"],noop)
		#if "sdk" in kwargs and kwargs["sdk"] == "prompt":
		#	self.window.show_quick_panel(["2.4.4","3.0.0-b1"],noop)

		# run build/release/simulate/test based on selected build command
		getattr(self, kwargs["do"])(**kwargs)

		self.panel.cleanup()

		sublime.status_message("Build Finished") # puts text at the bottom


		#self.window.show_input_panel("caption","initial text",noop,noop,noop)
		#sublime.message_dialog("thing")
		#self.panel.show_popup_menu(["foo","bar","baz"],noop)	
		#self.panel.show_popup("hey boss", sublime.COOPERATE_WITH_AUTO_COMPLETE, -1, 800, 800, noop, noop)
		#self.window.show_quick_panel(["a","b","c"],noop)


	def build(self, **kwargs):
		#todo: sdk string may be 1.4.x, need to match SDKTargets where x = .*
		self.panel.print("[building...]")
		if self.build_for == "application":
			cmd = self.compiler.compile("monkeyc")
		else:
			cmd = self.compiler.compile("barrelbuild")
		self.panel.print(cmd)
		#self.window.run_command("exec",{
		#	'shell_cmd':'sleep 5 && ls'
		#})
	def simulate(self, unit_test=False, **kwargs):
		self.panel.print("[running simulator]")
		cmd = self.simulator.simulate(os.path.join("build","App.prg"), "fenix5")
		self.panel.print(cmd)

	def package(self, **kwargs):
		""" SDK 3.0.0-b1 does not support exporting yet, 2.4.4 does"""
		self.panel.print("[packing for release...]")
		if self.build_for == "application":
			cmd = self.compiler.compile("monkeyc",flags="-r -e",name="App.iq")
		else:
			cmd = self.compiler.compile("barrelbuild")
		self.panel.print(cmd)

	def test(self, **kwargs):
		self.panel.print("[running tests]")
		if self.build_for == "application":
			cmd = self.compiler.compile("monkeyc",flags="-t")
		else:
			cmd = self.compiler.compile("barreltest", device="fenix5")
		self.panel.print(cmd)
		self.simulate(**kwargs)

	def detect_app_vs_barrel(self):
		""" Reads manifest.xml and detects if it is an application or barrel """
		return Manifest(self.vars['folder']).get_type()
		# could also check the .project file, or .settings/IQ_IDE.prefs


class Panel(object):
	"""wrapper for build output panel"""

	"""Alternatively, call "exec" with args like file_regex, syntax, etc"""

	def __init__(self, window):
		super(Panel, self).__init__()
		self.window = window
		self.view = self.window.create_output_panel('exec')

		panel_settings = self.view.settings()
		panel_settings.set("file_regex",r"([^:\n ]*):([0-9]+):(?:([0-9]+):)? (.*)$")
		panel_settings.set("line_numbers", False)
		panel_settings.set("gutter",False)
		panel_settings.set("scroll_past_end",False)
		#panel_settings.set("syntax","MonkeyCBuild.sublime-syntax") # alternate way
		self.view.set_syntax_file("MonkeyCBuild.sublime-syntax")

		show = sublime.load_settings("Preferences.sublime-settings").get("show_panel_on_build",True)
		#show=True # while still debugging
		if show:
			self.window.run_command("show_panel",{"panel":"output.exec"})
		self.view.set_read_only(False)

		# Default/exec.py calls create_output_panel a second time
		# after settings changes, to get picked up as result buffer?
		self.view = self.window.create_output_panel('exec')


		# panel debugging
		#self.print("Panel syntax: {}",self.view.settings().get('syntax'))
		
	def cleanup(self):
		self.view.set_read_only(True)

	def print(self, string, *args):
		if len(args):
			self.view.run_command("append",{"characters": string.format(*args)})
		else:
			self.view.run_command("append",{"characters": string})			
		self.view.run_command("append",{"characters":"\n"})

class Compiler(object):
	"""Generic wrapper class for compiling a monkeyc project"""


	""" @TODO: exit code message parsing with SDK/bin/compilerInfo.xml -> exitCodes """
	def __init__(self, sdk_path, project_path, key):
		super(Compiler, self).__init__()
		self.sdk_path = sdk_path
		self.project_path = project_path
		self.key=key
		
	def compile(self, compiler, name="App.prg", device=None, flags=None):
		cmd = "{compiler} -w -o {output} -f {jungle} {key} {device} {flags}"
		cmd = cmd.format(
			compiler=os.path.join(self.sdk_path,compiler),
			output=os.path.join(self.project_path,"build",name),
			jungle=os.path.join(self.project_path,"monkey.jungle"),
			key="-y {}".format(self.key,) if compiler in ["monkeyc","barreltest"] else "",
			device="-d {}".format(device,) if device else "",
			flags=flags if flags else ""
		)
		return cmd

class Simulator(object):
	"""Proxy for running CIQ apps in the simulator"""

	port=1234 # known connectiq simulator port

	def __init__(self, sdk_path):
		super(Simulator, self).__init__()
		self.sdk_path = sdk_path

	@classmethod
	def is_running(cls):
		""" Checks port 1234 to see if the simulator is running """
		with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
			try:
				sock.bind(("0.0.0.0",cls.port))
			except OSError:
				return True
			else:
				return False

	def start(self):
		pass
		# run `connectiq` from the sdk_path
		# in another thread

	def simulate(self, app, device):
		if not self.is_running():
			self.start()

		attempts=0
		while not self.is_running():
			attempts+=1
			if attempts > 40:
				sublime.message_dialog("could not connect to simulator")
				return

		cmd = "{monkeydo} {app} {device}"
		cmd = cmd.format(
			monkeydo=os.path.join(self.sdk_path,"monkeydo"),
			app=app,
			device=device
		)
		return cmd

"""
	Building:
		MC app:
			- select device (-d )
			- -s SDK target
			- -t unit tests
			- -r release (removes asserts)
"""


class SDKInput(sublime_plugin.ListInputHandler):

	def __init__(self, sdk_path, device):
		super(sublime_plugin.ListInputHandler, self).__init__()
		self.sdk_path= sdk_path
		self.selected_device = device

	def description(self, value, text):
		return text

	def name(self):
		"""argument keyname sent to command"""
		return "sdk"

	def placeholder(self):
		""" appears in command palette"""
		return "SDK Target"

	def confirm(self, value):
		pass

	def preview(self,item):
		""" appears at bottom of list, happens onChange """
		return sublime.Html("<b>"+item+"</b>")

	def list_items(self):
		# supported CIQ versions for device
		ciqs = SDK(self.sdk_path).getDevice(self.selected_device).ciq_versions
		# turn 1.3.2 into 1.3.x and remove duplicates with 'set'
		ciqs = list(set([".".join(c.split(".")[:2])+".x" for c in ciqs]))
		return ciqs

class DeviceInput(sublime_plugin.ListInputHandler):
	def __init__(self, sdk_path=None, path=None):
		super(sublime_plugin.ListInputHandler, self).__init__()
		self.device = ""
		if sdk_path:
			self.sdk_path = sdk_path
		if path:
			self.path = path

	def set_sdk(self, path):
		self.sdk_path = path
	def set_work_dir(self, path):
		self.path = path

	def description(self, value, text):
		""" when stacking inputs, this is shown as the "selected" text"""
		return text

	def name(self):
		"""argument keyname sent to command"""
		return "device"

	def initial_text(self):
		""" pre-populate response """
		return self.device

	def placeholder(self):
		""" appears in command palette"""
		return "Garmin Device"

	def confirm(self, value):
		self.device = value

	def preview(self,item):
		""" appears at bottom of list, happens onChange """
		device = SDK(self.sdk_path).getDevice(item)
		return sublime.Html("<b>{}</b>: {}".format(device.name,device.id))

	def list_items(self):
		""" values to choose from """
		return Manifest(self.path).devices()

	def next_input(self, args):
		return SDKInput(self.sdk_path, self.device)


# Random idea: sniff the TCP traffic between simulator and monkeydo.. ?