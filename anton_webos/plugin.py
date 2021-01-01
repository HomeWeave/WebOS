import json
import os.path
import time
from enum import Enum
from threading import Thread, Lock, Event

from pyantonlib.channel import GenericInstructionController
from pyantonlib.channel import GenericEventController
from pyantonlib.plugin import AntonPlugin
from pyantonlib.utils import log_info, log_warn
from anton.plugin_pb2 import PipeType
from anton.events_pb2 import GenericEvent, DeviceDiscoveryEvent
from anton.capabilities_pb2 import Capabilities

from pywebostv.connection import WebOSClient
from getmac import get_mac_address


class ConnectionStatus(Enum):
    NOT_STARTED = 0
    NOT_REGISTERED = 1
    PROMPTED = 2
    CONNECTED = 3


class JSONConfig:
    def __init__(self, path):
        self.lock = Lock()
        self.path = path
        if not os.path.isfile(path):
            self.config = {}
            self.write_config()

        with open(path, "r") as inp:
            self.config = json.load(inp)

    def __getitem__(self, key):
        with self.lock:
            return self.config[key]

    def get(self, key):
        return self.config.get(key)

    def __setitem__(self, key, value):
        with self.lock:
            self.config[key] = value
            self.write_config_internal()

    def write_config(self):
        with self.lock:
            self.write_config_internal()

    def write_config_internal(self):
        with open(self.path, "w") as out:
            json.dump(self.config, out)


class TVController(object):
    def __init__(self, client, config, mac, send_event):
        self.client = client
        self.config = config
        self.mac = mac
        self.status = ConnectionStatus.NOT_STARTED
        self.send_event = send_event
        self.reg_data = None

    def start(self):
        self.client.connect()
        self.reg_data = self.config.get(self.mac) or {}
        if not self.reg_data:
            self.status = ConnectionStatus.NOT_REGISTERED

            # If not registered, send event with registration capability.
            event = GenericEvent()
            disc = event.discovery
            disc.vendor_device_id=self.mac
            disc.capabilities.device_registration_capabilities.total_steps = 2
            self.send_event(event)
        else:
            self.register_tv()

    def register_tv(self):
        for status in self.client.register(self.reg_data):
            if status == WebOSClient.PROMPTED:
                self.status = ConnectionStatus.PROMPTED
                # TODO: Think about timeout (Prompt expiry)
                event = GenericEvent()
                event.device_registration.prompt_text = "Check your TV."
                self.send_event(event)
            elif status == WebOSClient.REGISTERED:
                self.status = CONNECTED
                self.config[self.mac] = self.reg_data
                event = GenericEvent()
                event.device_registration.success_text = "Successful!"
                self.send_event(event)

    def execute_instruction(self, instruction):
        pass


class TVDiscovery(object):
    def __init__(self, config, send_event):
        self.discovery_lock = Lock()
        self.discovered_devices = {}
        self.config = config
        self.send_event = send_event
        self.discovery_thread = Thread(target=self.start)
        self.stop_event = Event()

    def start(self):
        first_iter = True
        while first_iter or not self.stop_event.wait(timeout=5 * 60):
            first_iter = False
            with self.discovery_lock:
                log_info("Attempting to discover LG TVs..")
                clients = WebOSClient.discover()
                if not clients:
                    log_info("No LG TVs found.")
                    continue
                for client in WebOSClient.discover():
                    mac = get_mac_address(hostname=client.host)
                    if mac in self.discovered_devices:
                        continue

                    log_info("Found a TV at: " + client.host)
                    tv_controller= TVController(client, self.config, mac,
                                                self.send_event)
                    self.discovered_devices[mac] = tv_controller
                    tv_controller.start()

    def stop(self):
        self.stop_event.set()


class WebOSPlugin(AntonPlugin):
    def setup(self, plugin_startup_info):
        config = JSONConfig(plugin_startup_info.data_dir + "/config.json")
        registry = self.channel_registry()

        event_controller = GenericEventController(self.reader())
        registry.register_controller(PipeType.IOT_EVENTS, event_controller)
        self.send_event = event_controller.send

        instruction_controller = GenericInstructionController(self.reader(), {})
        registry.register_controller(PipeType.IOT_INSTRUCTION,
                                     instruction_controller)

        self.discovery = TVDiscovery(config, self.send_event)
        log_info("WebOS Plugin setup complete.")

    def on_start(self):
        log_info("Starting ..")
        self.discovery.start()

    def on_stop(self):
        pass

