import json
import os.path
import time
from enum import Enum
from threading import Thread, Lock, Event

from pyantonlib.plugin import AntonPlugin
from pyantonlib.channel import AppHandlerBase
from pyantonlib.channel import DefaultProtoChannel
from pyantonlib.utils import log_info, log_warn
from anton.plugin_pb2 import PipeType
from anton.ui_pb2 import CustomMessage, DynamicAppRequestType
from anton.plugin_messages_pb2 import GenericPluginToPlatformMessage

from anton_webos.settings import Settings
from anton_webos.devices import DevicesController

from pywebostv.controls import SystemControl, ApplicationControl
from wakeonlan import send_magic_packet


class Channel(DefaultProtoChannel):
    pass


class AppHandler(AppHandlerBase):

    def __init__(self, plugin_startup_info):
        super().__init__(plugin_startup_info, incoming_message_key='action')
        # Will be set if TV is registered.
        self.devices_controller = None

    def set_device_controller(self, device_controller):
        self.devices_controller = device_controller

        self.register_action(
            'discover',
            lambda requester_id, _: self.devices_controller.start_discovery)
        self.register_action('get_all_devices',
                             self.devices_controller.send_all_devices)
        self.register_action('register',
                             self.devices_controller.register_device)

    def get_ui_path(self, app_type):
        if app_type == DynamicAppRequestType.SETTINGS:
            return "ui/settings_ui.pbtxt"


class WebOSPlugin(AntonPlugin):

    def setup(self, plugin_startup_info):
        settings = Settings(plugin_startup_info.data_dir)
        registrar = self.channel_registrar()

        self.app_handler = AppHandler(plugin_startup_info)
        self.devices_controller = DevicesController(settings)
        self.app_handler.set_device_controller(self.devices_controller)
        self.devices_controller.set_app_handler(self.app_handler)

        self.channel = Channel(self.devices_controller, self.app_handler)

        registrar.register_controller(PipeType.DEFAULT, self.channel)

        log_info("WebOS Plugin setup complete.")

    def on_start(self):
        log_info("Starting ..")
        self.devices_controller.start()

    def on_stop(self):
        self.devices_controller.stop()
