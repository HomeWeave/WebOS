from threading import Thread

from pyantonlib.utils import log_info

from pywebostv.connection import WebOSClient
from getmac import get_mac_address


class WebOsRegistrationController:

    def __init__(self, settings, callback):
        self.settings = settings
        self.callback = callback
        self.conns = {}

    def start_discovery(self):
        Thread(target=self.discover).start()

    def discover(self):
        self.callback({"discovery_status": "discovering"})

        log_info("Discovering WebOS devices..")
        clients = WebOSClient.discover()
        if not clients:
            log_info("No LG TVs found.")
            self.callback({"discovery_status": "not found"})
            return

        self.conns = {
            get_mac_address(client.host): {
                "conn": client,
                "host": client.host,
            }
            for client in clients
        }
        self.callback({
            "discovery_status":
            "found",
            "devices": [
                dict(id=did, host=data["host"])
                for did, host in self.conn.items()
            ]
        })

    def register_all(self):
        self.discover()
        for device_id in self.conns:
            self.register(device_id)

    def register(self, device_id):
        if device_id not in self.conns:
            raise ResourceNotFound(device_id)

        data = self.conns[device_id]

        config = self.settings.get_prop("devices", default={})
        device_config = config.get(device_id, default={})
        store = device_config.get('login', default={})

        registration_generator = data["conn"].register(store)

        Thread(target=self.process_registration,
               args=(device_id, data, store, device_config, config,
                     registration_generator)).start()

    def process_registration(self, device_id, data, store, device_config,
                             config, registration_generator):
        try:
            for status in registration_generator:
                if status == WebOSClient.PROMPTED:
                    self.callback({
                        "registration_status": "prompted",
                        "device_id": device_id
                    })
                elif status == WebOSClient.REGISTERED:
                    device_config['login'] = data["store"]
                    config[device_id] = device_config
                    self.settings.set_prop("devices", config)

                    self.callback({
                        "registration_status": "registered",
                        "conn": data["conn"],
                        "device_id": device_id
                    })
        except Exception as e:
            self.callback({
                "registration_status": "failed",
                "device_id": device_id
            })
            raise AntonInternalError("Unable to register: " + str(e))

    def stop(self):
        for client in self.conns:
            client["conn"].close()
        self.discovery_thread.join()
