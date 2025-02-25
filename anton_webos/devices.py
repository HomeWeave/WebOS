from threading import Thread, Event

from anton.capabilities_pb2 import Capabilities

from pyantonlib.channel import DeviceHandlerBase

from anton_webos.registration import WebOsRegistrationController


class TVController(object):

    def __init__(self, client, config, mac, send_event,
                 instruction_controller):
        self.client = client
        self.host = client.host
        self.config = config
        self.mac = mac
        self.send_event = send_event
        self.instruction_controller = instruction_controller
        self.reg_data = None
        self.register_tv_thread = Thread(target=self.register_tv)

        self.app_control = ApplicationControl(client)

    def start(self):
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
        if device_info['is_registered'] and device_id not in self.devices:
            self.devices[device_id] = WebOSController(device_info['conn'],
                                                      self, device_info)
            log_info("Connected to WebOS TV at: " + device_info['conn'].host)
            self.devices[device_id].start()

        if requester_id:
            self.send_all_devices(requester_id=requester_id)

    def handle_set_device_state(self, msg, responder):
        pass
