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
from anton.events_pb2 import GenericEvent
from anton.device_pb2 import DEVICE_KIND_TV, DEVICE_STATUS_UNREGISTERED
from anton.device_pb2 import DEVICE_STATUS_ONLINE, DEVICE_STATUS_OFFLINE
from anton.power_pb2 import POWER_OFF, POWER_ON
from anton.capabilities_pb2 import Capabilities

from pywebostv.connection import WebOSClient
from pywebostv.controls import SystemControl
from getmac import get_mac_address
from wakeonlan import send_magic_packet


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
    def __init__(self, client, config, mac, send_event, instruction_controller):
        self.client = client
        self.config = config
        self.mac = mac
        self.send_event = send_event
        self.instruction_controller = instruction_controller
        self.reg_data = None
        self.register_tv_thread = Thread(target=self.register_tv)

    def start(self):
        self.client.connect()
        self.reg_data = self.config.get(self.mac) or {}
        if not self.reg_data:
            # If not registered, send event with registration capability.
            event = GenericEvent(device_id=self.mac)
            event.device.friendly_name = "LG TV"
            event.device.device_kind = DEVICE_KIND_TV
            event.device.device_status = DEVICE_STATUS_UNREGISTERED

            capabilities = event.device.capabilities
            capabilities.device_registration_capabilities.greeting_text = (
                    "Web OS TV discovered.")
            capabilities.device_registration_capabilities.action_text = (
                    "Register")
            self.send_event(event)
        else:
            self.register_tv()

    def stop(self):
        self.client.close()

    def register_tv(self):
        for status in self.client.register(self.reg_data):
            if status == WebOSClient.PROMPTED:
                # TODO: Think about timeout (Prompt expiry)
                event = GenericEvent(device_id=self.mac)
                capabilities = event.device.capabilities
                capabilities.device_registration_capabilities.greeting_text = (
                        "Please accept the prompt on the TV.")
                self.send_event(event)
            elif status == WebOSClient.REGISTERED:
                self.config[self.mac] = self.reg_data
                event = GenericEvent(device_id=self.mac)
                event.device.device_kind = DEVICE_KIND_TV
                event.device.device_status = DEVICE_STATUS_ONLINE
                capabilities = event.device.capabilities
                capabilities.power_state.supported_power_states[:] = [POWER_OFF,
                                                                      POWER_ON]
                notification = capabilities.notifications
                notification.simple_text_notification_supported = True

                self.send_event(event)

    def on_device_instruction(self, instruction):
        if instruction.device.device_registration_instruction.execute_step == 1:
            self.register_tv_thread.start()

    def on_power_instruction(self, instruction):
        power_instruction = instruction.power_state

        if power_instruction == POWER_OFF:
            system = SystemControl(self.client)
            system.power_off()
        elif power_instruction == POWER_ON:
            send_magic_packet(self.mac)

        event = GenericEvent(device_id=light.unique_id)
        event.power_state.power_state = power_instruction
        self.send_event(event)


class TVDiscovery(object):
    def __init__(self, config, send_event, instruction_controller, devices):
        self.devices = devices
        self.config = config
        self.send_event = send_event
        self.instruction_controller = instruction_controller
        self.discovery_thread = Thread(target=self.run)
        self.stop_event = Event()

    def start(self):
        self.discovery_thread.start()

    def run(self):
        first_iter = True
        while first_iter or not self.stop_event.wait(timeout=5 * 60):
            first_iter = False
            log_info("Attempting to discover LG TVs..")

            clients = WebOSClient.discover()
            if not clients:
                log_info("No LG TVs found.")
                continue
            for client in clients:
                mac = get_mac_address(hostname=client.host)
                if mac in self.devices:
                    continue

                log_info("Found a TV at: " + client.host)
                tv_controller = TVController(
                        client, self.config, mac, self.send_event,
                        self.instruction_controller)
                self.devices[mac] = tv_controller
                tv_controller.start()

    def stop(self):
        self.stop_event.set()
        self.discovery_thread.join()


class WebOSPlugin(AntonPlugin):
    def setup(self, plugin_startup_info):
        config = JSONConfig(plugin_startup_info.data_dir + "/config.json")
        registrar = self.channel_registrar()

        event_controller = GenericEventController(lambda call_status: 0)
        self.send_event = event_controller.create_client(0, self.on_response)
        registrar.register_controller(PipeType.IOT_EVENTS, event_controller)

        instruction_controller = GenericInstructionController({
            "device": lambda obj: self.forward_instruction(
                                      "on_device_instruction", obj),
            "power_state": lambda obj: self.forward_instruction(
                                           "on_power_instruction", obj),
        })
        registrar.register_controller(PipeType.IOT_INSTRUCTION,
                                      instruction_controller)

        self.devices = {}

        self.discovery = TVDiscovery(config, self.send_event,
                                     instruction_controller, self.devices)

        log_info("WebOS Plugin setup complete.")

    def on_start(self):
        log_info("Starting ..")
        self.discovery.start()

    def on_stop(self):
        self.discovery.stop()
        for mac, tv_controller in self.devices.items():
            tv_controller.stop()


    def on_response(self, call_status):
        print("Received response:", call_status)

    def forward_instruction(self, instruction_type, instruction):
        device_id = instruction.device_id

        if device_id not in self.devices:
            log_warn("Dropping instruction, unknown device: " + device_id)
            return

        func = getattr(self.devices[device_id], instruction_type, None)
        if func:
            func(instruction)
