from threading import Thread, Event

from anton.capabilities_pb2 import Capabilities
from anton.power_pb2 import PowerState
from anton.state_pb2 import DeviceState
from anton.device_pb2 import DeviceKind, DeviceStatus

from pyantonlib.channel import DeviceHandlerBase
from pyantonlib.utils import log_info, log_warn

from anton_webos.registration import WebOsRegistrationController

from pywebostv.controls import (ApplicationControl, MediaControl,
                                SystemControl, InputControl, TvControl,
                                SourceControl)
from wakeonlan import send_magic_packet


class BaseController:

    def __init__(self, client, devices_controller, client_info):
        self.client = client
        self.devices_controller = devices_controller
        self.client_info = client_info

    def on_start(self, capabilities):
        pass

    def on_stop(self):
        pass

    def handle_set_device_state(self, msg, responder):
        pass


class ApplicationController(BaseController):

    def __init__(self, client, devices_controller, client_info):
        super().__init__(client, devices_controller, client_info)
        self.app_control = ApplicationControl(client)

    def on_start(self, state, capabilities):
        apps_capabilities = capabilities.apps
        apps_capabilities.can_switch_apps = True
        apps_capabilities.has_installed_apps = True

        for app in self.app_control.list_apps():
            app_msg = state.installed_apps.add()
            app_msg.app_name = app["title"]
            app_msg.app_id = app["id"]
            app_msg.app_icon_url = app["icon"]

        state.foreground_app_id = self.app_control.get_current()

        self.app_control.subscribe_get_current(self.on_foreground_app_change)

    def on_stop(self):
        self.app_control.unsubscribe_get_current()

    def on_foreground_app_change(self, success, app_id):
        if success:
            self.devices_controller.send_device_state_updated(
                DeviceState(device_id=self.client_info['id'],
                            foreground_app_id=app_id))


class SystemController(BaseController):

    def __init__(self, client, devices_controller, client_info):
        super().__init__(client, devices_controller, client_info)
        self.system_control = SystemControl(client)

    def on_start(self, state, capabilities):
        capabilities.power_state.supported_power_states[:] = [
            PowerState.POWER_STATE_OFF, PowerState.POWER_STATE_ON
        ]
        capabilities.notifications.simple_text_notification_supported = True

        state.power_state = PowerState.POWER_STATE_ON

    def handle_set_device_state(self, msg, responder):
        if msg.power_state == PowerState.POWER_STATE_OFF:
            log_info("Turning off TV.")
            self.system_control.power_off()


class PowerOffWebOSController(object):

    def __init__(self, device_id, devices_controller):
        self.device_id = device_id
        self.devices_controller = devices_controller

    def start(self):
        state = DeviceState(device_id=self.device_id,
                            friendly_name="Offline WebOS device",
                            kind=DeviceKind.DEVICE_KIND_TV,
                            power_state=PowerState.POWER_STATE_OFF,
                            device_status=DeviceStatus.DEVICE_STATUS_ONLINE)
        state.capabilities.power_state.supported_power_states[:] = [
            PowerState.POWER_STATE_ON, PowerState.POWER_STATE_OFF
        ]
        self.devices_controller.send_device_state_updated(state)

    def handle_set_device_state(self, msg, responder):
        if msg.power_state == PowerState.POWER_STATE_ON:
            send_magic_packet(self.device_id)


class WebOSController(object):

    def __init__(self, client, devices_controller, client_info):
        self.client = client
        self.devices_controller = devices_controller
        self.client_info = client_info

        self.app_control = ApplicationController(client, devices_controller,
                                                 client_info)
        self.system_control = SystemController(client, devices_controller,
                                               client_info)
        self.all_controls = [self.app_control, self.system_control]

    def start(self):
        state = DeviceState(
            device_id=self.client_info['id'],
            friendly_name=self.system_control.system_control.info()
            ["product_name"],
            kind=DeviceKind.DEVICE_KIND_TV,
            device_status=DeviceStatus.DEVICE_STATUS_ONLINE)
        for control in self.all_controls:
            control.on_start(state, state.capabilities)

        self.devices_controller.send_device_state_updated(state)

    def stop(self):
        for control in self.all_controls:
            try:
                control.on_stop()
            except Exception as e:
                log_warn("Ignoring: ", e)
                pass
        self.client.close()

        state = DeviceState(device_id=self.client_info['id'],
                            device_status=DeviceStatus.DEVICE_STATUS_ONLINE,
                            power_state=PowerState.POWER_STATE_OFF)
        self.devices_controller.send_device_state_updated(state)

    def handle_set_device_state(self, msg, responder):
        for controller in self.all_controls:
            controller.handle_set_device_state(msg, responder)


class DevicesController(DeviceHandlerBase):

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.registration_controller = WebOsRegistrationController(settings)
        self.devices = {}
        # Will be set outside of this class.
        self.app_handler = None
        self.connect_thread_stop = None
        self.connect_thread = None

    def start(self):
        self.connect_thread = Thread(target=self.background_connect)
        self.connect_thread_stop = Event()
        self.connect_thread.start()

    def stop(self):
        self.connect_thread_stop.set()
        self.connect_thread.join()
        self.registration_controller.stop()

    def send_all_devices(self, requester_id, _=None):

        def make_status(info):
            if info["is_online"] and info["is_registered"]:
                return "Connected"
            elif info["is_online"] and not info["is_registered"]:
                return "Online, unregistered"
            elif not info["is_online"]:
                return "Offline"
            else:
                return "Unknown"

        def make_info(info):
            return dict(status=info.get('status', make_status(info)),
                        **{
                            x: y
                            for x, y in info.items()
                            if x not in ('conn', 'status')
                        })

        resp = {
            "type":
            "devices",
            "devices": [
                make_info(info) for info in
                self.registration_controller.get_all_devices().values()
            ]
        }
        self.app_handler.send_message(resp, requester_id=requester_id)

    def background_connect(self):
        while True:
            self.registration_controller.register_known_devices(
                lambda device_info: self.on_device_status_changed(device_info))
            if self.connect_thread_stop.wait(timeout=20):
                break

    def set_app_handler(self, app_handler):
        self.app_handler = app_handler

    def discover(self):
        self.registration_controller.discover()

    def register_device(self, requester_id, request):
        self.registration_controller.register(
            request.get('device_id'),
            lambda device_info: self.on_device_status_changed(
                device_info, requester_id))

    def on_device_status_changed(self, device_info, requester_id=None):
        device_id = device_info['id']
        if not device_info['is_registered']:
            return

        device = self.devices.get(device_id)
        if device_info['is_connected']:
            if not isinstance(device, WebOSController):
                conn = device_info['conn']
                self.devices[device_id] = WebOSController(
                    conn, self, device_info)
                self.devices[device_id].start()
                log_info("Connected to WebOS TV at: " +
                         device_info['conn'].host)
        else:
            if not isinstance(device, PowerOffWebOSController):
                if isinstance(device, WebOSController):
                    device.stop()
                self.devices[device_id] = PowerOffWebOSController(
                    device_id, self)
                self.devices[device_id].start()

        if requester_id:
            self.send_all_devices(requester_id=requester_id)

    def handle_set_device_state(self, msg, responder):
        log_info("Handling set_device_state: " + str(msg))
        device = self.devices.get(msg.device_id)
        if device is None:
            raise ResourceNotFound(msg.device_id)

        device.handle_set_device_state(msg, responder)
