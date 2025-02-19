from threading import Thread, Event

from anton.capabilities_pb2 import Capabilities

from pyantonlib.channel import DeviceHandlerBase

from anton_webos.registration import WebOsRegistrationController


class TVController(object):

    def __init__(self, client, config, mac, send_event,
                 instruction_controller):
        self.client = client
        self.config = config
        self.mac = mac
        self.send_event = send_event
        self.instruction_controller = instruction_controller
        self.reg_data = None
        self.register_tv_thread = Thread(target=self.register_tv)

        self.app_control = ApplicationControl(client)

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
                capabilities.power_state.supported_power_states[:] = [
                    POWER_OFF, POWER_ON
                ]
                notification = capabilities.notifications
                notification.simple_text_notification_supported = True

                apps_capabilities = capabilities.apps
                apps_capabilities.can_switch_apps = True
                apps_capabilities.has_installed_apps = True

                self.send_event(event)

                # send apps list.
                event = GenericEvent(device_id=self.mac)
                apps_state = event.apps.apps_state

                for app in self.app_control.list_apps():
                    app_msg = apps_state.installed_apps.add()
                    app_msg.app_name = app["title"]
                    app_msg.app_id = app["id"]
                    app_msg.app_icon_url = app["icon"]

                apps_state.foreground_app_id = self.app_control.get_current()
                self.send_event(event)

                # Subscribe to events
                self.app_control.subscribe_get_current(self.on_app_change)

    def on_app_change(self, success, app_id):
        event = GenericEvent(device_id=self.mac)
        event.apps.foreground_app.app_id = app_id

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

        event = GenericEvent(device_id=self.mac)
        event.power_state.power_state = power_instruction
        self.send_event(event)


class DevicesController(DeviceHandlerBase):

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.registration_controller = WebOsRegistrationController(
            settings, self.handle_registration_callback)
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

    def background_connect(self):
        while True:
            self.registration_controller.register_all()
            if self.connect_thread_stop.wait(timeout=60):
                break

    def set_app_handler(self, app_handler):
        self.app_handler = app_handler

    def discover(self):
        self.registration_controller.discover()

    def handle_registration_callback(self, obj):
        if "registration_status" in obj:
            conn = obj["registration_status"].pop('conn')
            self.devices[obj["device_id"]] = conn

        self.app_handler.send_message(obj, requester_id=None)

    def handle_set_device_state(self, msg, responder):
        pass
