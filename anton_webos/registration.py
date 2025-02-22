from threading import Thread

from pyantonlib.utils import log_info

from pywebostv.connection import WebOSClient
from getmac import get_mac_address


class WebOsRegistrationController:

    def __init__(self, settings):
        self.settings = settings

        known_devices = self.settings.get_prop('known_devices', default=[])
        self.conns = {x: {"id": x, "is_online": False} for x in known_devices}

    def start_discovery(self):
        Thread(target=self.discover).start()

    def discover(self):
        log_info("Discovering WebOS devices..")
        clients = WebOSClient.discover()
        if not clients:
            log_info("No LG TVs found.")
            return
        else:
            log_info(f"Found {len(clients)} clients.")

        for client in clients:
            did = get_mac_address(hostname=client.host)
            self.conns[did] = {
                "id": did,
                "conn": client,
                "host": client.host,
                "is_online": True,
                "is_registered": False
            }

    def get_all_devices(self):
        return self.conns

    def register_known_devices(self, callback):
        self.discover()
        known_devices = self.settings.get_prop('known_devices', default=[])
        for device_id in self.conns:
            if device_id in known_devices:
                self.register(device_id, callback)

    def register(self, device_id, callback):
        if device_id not in self.conns:
            raise ResourceNotFound(device_id)

        data = self.conns[device_id]

        config = self.settings.get_prop("devices", default={})
        device_config = config.get(device_id, default={})
        store = device_config.get('login', default={})

        registration_generator = data["conn"].register(store)

        Thread(target=self.process_registration,
               args=(device_id, data, store, device_config, config,
                     registration_generator, callback)).start()

    def process_registration(self, device_id, data, store, device_config,
                             config, registration_generator, callback):
        try:
            for status in registration_generator:
                if status == WebOSClient.PROMPTED:
                    self.conns[device_id]["status"] = "Registration initiated."
                    callback()
                elif status == WebOSClient.REGISTERED:
                    device_config['login'] = data["store"]
                    config[device_id] = device_config
                    self.settings.set_prop("devices", config)

                    known_devices = self.settings.get_prop('known_devices',
                                                           default=[])
                    known_devices = list(set(known_devices) | {device_id})
                    self.settings.set_prop('known_devices', known_devices)

                    self.conns[device_id]["status"] = "Connected."
                    callback()
        except Exception as e:
            callback({"registration_status": "failed", "device_id": device_id})
            raise AntonInternalError("Unable to register: " + str(e))

    def stop(self):
        for client in self.conns:
            client["conn"].close()
        self.discovery_thread.join()
