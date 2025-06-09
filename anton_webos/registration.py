from threading import Thread

from pyantonlib.utils import log_info, log_warn

import ssl

ssl.wrap_socket = ssl.SSLContext().wrap_socket

from pywebostv.connection import WebOSClient
from getmac import get_mac_address


def get_known_devices(settings):
    return settings.get_prop('known_devices', default={})


def add_known_devices(settings, device_info):
    props = {"id", "is_registered", "store"}
    devices = get_known_devices(settings)
    devices[device_info["id"]] = {
        x: y
        for x, y in device_info.items() if x in props
    }
    settings.set_prop('known_devices', devices)


class WebOsRegistrationController:

    def __init__(self, settings):
        self.settings = settings

        self.conns = {}
        for key, value in get_known_devices(self.settings).items():
            value["is_online"] = False
            value["is_connected"] = False
            self.conns[key] = value

    def discover(self, offline_callback=None):
        log_info("Discovering WebOS devices..")
        clients = WebOSClient.discover(secure=True)
        if not clients:
            log_info("No LG TVs found.")
        else:
            log_info(f"Found {len(clients)} clients.")

        discovered_device_ids = set()
        for client in clients:
            did = get_mac_address(hostname=client.host)
            discovered_device_ids.add(did)
            if did in self.conns:
                self.conns[did].update({
                    "conn": client,
                    "host": client.host,
                    "is_online": True
                })
                continue

            self.conns[did] = {
                "id": did,
                "conn": client,
                "host": client.host,
                "is_online": True,
                "is_registered": False,
                "store": {}
            }
            log_info(f"Discovered a new device: {self.conns[did]}")

        for key, cur_conn in self.conns.items():
            if key not in discovered_device_ids and cur_conn['is_connected']:
                cur_conn['is_connected'] = False
                log_info(f"Existing device is now offline: {cur_conn}")
                if offline_callback:
                    offline_callback(cur_conn)

    def get_all_devices(self):
        return self.conns

    def register_known_devices(self, callback):
        # For devices from the config file, devices.py will start up a
        # PowerOffWebOSController.
        for device_id, device_info in self.conns.items():
            callback(device_info)

        self.discover(offline_callback=callback)
        for device_id, device_info in self.conns.items():
            if (device_info['is_registered'] and device_info['is_online']
                    and not device_info['is_connected']):
                self.process_registration(device_id, callback)

    def register(self, device_id, callback):
        if device_id not in self.conns:
            raise ResourceNotFound(device_id)

        Thread(target=self.process_registration,
               args=(device_id, device_id, callback)).start()

    def process_registration(self, device_id, callback):
        device_info = self.conns[device_id]
        log_info(f"Attempting registration with: {device_info}")

        conn = device_info["conn"]
        conn.connect()
        registration_generator = conn.register(device_info['store'])

        try:
            for status in registration_generator:
                log_warn("Status: " + str(status))
                if status == WebOSClient.PROMPTED:
                    device_info["status"] = "Registration initiated"
                    log_info(f"Registration initiated for: {device_info}")
                    callback(device_info)
                elif status == WebOSClient.REGISTERED:
                    device_info["status"] = "Connected"
                    device_info["is_registered"] = True
                    device_info["is_connected"] = True
                    log_info(f"Registration succeeded for: {device_info}")
                    add_known_devices(self.settings, device_info)
                    callback(device_info)
        except Exception as e:
            log_warn("Registration failed: " + str(e))
            device_info["status"] = "Registration failed."
            callback(device_info)
            raise e

    def stop(self):
        for client in self.conns:
            client["conn"].close()
