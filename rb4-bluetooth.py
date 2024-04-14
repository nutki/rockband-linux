#!/bin/env python3

import select
import sys
import os
import struct
import fcntl

from evdev import UInput, AbsInfo, ecodes as uinput
import pyudev
import functools

YELLOW_CYMBAL = 47
BLUE_CYMBAL = 48
GREEN_CYMBAL = 49
PICKUP = 43
WHAMMY = 44
TILT = 45
MAD_CATZ_VENDOR_ID = 1848
GUITAR_PRODUCT_ID = 33377
DRUMS_PRODUCT_ID = 33378

drum_events = { uinput.EV_KEY: [ uinput.BTN_A, uinput.BTN_B, uinput.BTN_C, uinput.BTN_X, uinput.BTN_Y, uinput.BTN_Z,
    uinput.BTN_TL, uinput.BTN_TR, uinput.BTN_TL2, uinput.BTN_TR2, uinput.BTN_SELECT, uinput.BTN_START, uinput.BTN_MODE ],
    uinput.EV_ABS: [ (uinput.ABS_HAT0X, AbsInfo(0,-1,1,0,0,0)), (uinput.ABS_HAT0Y, AbsInfo(0,-1,1,0,0,0))]}
guitar_events = {
    uinput.EV_KEY: drum_events[uinput.EV_KEY],
    uinput.EV_ABS: drum_events[uinput.EV_ABS] + [(uinput.ABS_X, AbsInfo(0,0,255,0,0,0)), (uinput.ABS_Y, AbsInfo(0,0,255,0,0,0)), (uinput.ABS_Z, AbsInfo(0,0,4,0,0,0))]
}

def _ioctl(fd, code, return_type):
    size = struct.calcsize(return_type)
    return struct.unpack(return_type, fcntl.ioctl(fd, code | (size << 16), "\x00" * size))

_IOC_EVIOCGRAB = 0x4590 | (1 << 30)
_IOC_EVIOCGID = 0x4502 | (2 << 30)
_IOC_EVIOCGNAME = 0x4506 | (2 << 30)
_IOC_HIDIOCGRAWINFO = 0x4803 | (2 << 30)
_IOC_HIDIOCGRAWNAME = 0x4804 | (2 << 30)

def _EVIOCGRAB(fd):
    _ioctl(fd, _IOC_EVIOCGRAB, "I")
def _EVIOCGID(fd):
    return _ioctl(fd, _IOC_EVIOCGID, "HHHH")
def _HIDIOCGRAWNAME(fd):
    ret = _ioctl(fd, _IOC_HIDIOCGRAWNAME, "c" * 1024)
    return "".join(b"".join(ret).decode("utf-8")).rstrip("\x00")
def _EVIOCGNAME(fd):
    ret = _ioctl(fd, _IOC_EVIOCGNAME, "c" * 1024)
    return "".join(b"".join(ret).decode("utf-8")).rstrip("\x00")
def _HIDIOCGRAWINFO(fd):
    return _ioctl(fd, _IOC_HIDIOCGRAWINFO, "iHH")

def is_rb4_device(fname):
    if fname.startswith("hidraw"):
        with open(f"/dev/{fname}") as fd:
            bus_type, vendor_id, product_id = _HIDIOCGRAWINFO(fd)
            if vendor_id == MAD_CATZ_VENDOR_ID and (product_id == DRUMS_PRODUCT_ID or product_id == GUITAR_PRODUCT_ID):
                return True
    return False

def is_rb4_ev_device(fname):
    try:
        if fname.startswith("event"):
            with open(f"/dev/input/{fname}") as fd:
                bus_type, vendor_id, product_id, version = _EVIOCGID(fd)
                if vendor_id == MAD_CATZ_VENDOR_ID and (product_id == DRUMS_PRODUCT_ID or product_id == GUITAR_PRODUCT_ID):
                     return True
    except OSError:
        return False
    return False

class MappedDevice:
    def __init__(self, fd):
        name = _HIDIOCGRAWNAME(fd)
        product_id = _HIDIOCGRAWINFO(fd)[2]
        self.is_drumset = product_id == DRUMS_PRODUCT_ID
        print(f"Found {fd.name} - {name}")
        if self.is_drumset:
            self.udevice = UInput(drum_events, name="Mapped Rock Band 4 Drum Set", vendor=0, product=0)
        else:
            self.udevice = UInput(guitar_events, name="Mapped Rock Band 4 Fender Stratocaster", vendor=0, product=0)
        self.udevice_state = {}
        self.needs_syn = False
    def emit(self, ev, value, evtype = uinput.EV_KEY):
        if ev in self.udevice_state and self.udevice_state[ev] == value:
            return
        self.udevice_state[ev] = value
        self.udevice.write(evtype, ev, value)
        self.needs_syn = True
    def syn(self):
        if self.needs_syn:
            self.needs_syn = False
            self.udevice.syn()

if __name__ == "__main__":
    try:
        devices = {}
        poll = select.poll()
        device_fds = [open(f"/dev/{fname}") for fname in filter(is_rb4_device, os.listdir("/dev/"))]
        for fd in device_fds:
            poll.register(fd, select.POLLIN)
            devices[fd.fileno()] = MappedDevice(fd)
        grab_fds = [open(f"/dev/input/{fname}") for fname in filter(is_rb4_ev_device, os.listdir("/dev/input"))]
        for fd in grab_fds:
            print(f"Grabbing device {fd.name} - {_EVIOCGNAME(fd)}")
            _EVIOCGRAB(fd)
        monitor = pyudev.Monitor.from_netlink(pyudev.Context())
        monitor.filter_by(subsystem='input')
        monitor.filter_by(subsystem='hidraw')
        monitor.start()
        poll.register(monitor, select.POLLIN)
        while True:
            for fd, event in poll.poll():
                if fd == monitor.fileno():
                    for d in iter(functools.partial(monitor.poll, 0), None):
                        if d.action == "add" and d.subsystem == "hidraw" and is_rb4_device(d.sys_name):
                            fd = open(d.device_node)
                            poll.register(fd, select.POLLIN)
                            devices[fd.fileno()] = MappedDevice(fd)
                            device_fds.append(fd)
                        if d.action == "add" and d.subsystem == "input" and is_rb4_ev_device(d.sys_name):
                            fd = open(d.device_node)
                            _EVIOCGRAB(fd)
                            grab_fds.append(fd)
                    continue
                device = devices[fd]
                is_drumset = device.is_drumset
                try:
                    data = os.read(fd, 4096)
                except OSError as err:
                    print(err)
                    poll.unregister(fd)
                    device.udevice.close()
                # print(data.hex().replace('0', '_'))
                hat0x = [0,1,1,1,0,-1,-1,-1,0][data[5]&15]
                hat0y = [-1,-1,0,1,1,1,0,-1,0][data[5]&15]
                device.emit(uinput.ABS_HAT0X, hat0x, uinput.EV_ABS)
                device.emit(uinput.ABS_HAT0Y, hat0y, uinput.EV_ABS)
                cymbal_g = data[GREEN_CYMBAL] > 0 if is_drumset else 0
                cymbal_b = data[BLUE_CYMBAL] > 0 if is_drumset else 0
                cymbal_y = data[YELLOW_CYMBAL] > 0 if is_drumset else 0
                device.emit(uinput.BTN_B, 0 if cymbal_g else (data[5] >> 5) & 1)
                device.emit(uinput.BTN_C, (data[5] >> 6) & 1)
                device.emit(uinput.BTN_X, 0 if cymbal_y else (data[5] >> 7) & 1)
                device.emit(uinput.BTN_A, 0 if cymbal_b else (data[5] >> 4) & 1)
                device.emit(uinput.BTN_TL2, (data[6] >> 4) & 1)
                device.emit(uinput.BTN_TR2, (data[6] >> 5) & 1)
                device.emit(uinput.BTN_Y, (data[6] >> 0) & 1)
                device.emit(uinput.BTN_MODE, data[7] & 1)
                if is_drumset:
                    device.emit(uinput.BTN_TL, int(cymbal_b))
                    device.emit(uinput.BTN_TR, int(cymbal_g))
                    device.emit(uinput.BTN_SELECT, int(cymbal_y))
                    device.emit(uinput.BTN_Z, (data[6] >> 1) & 1)
                else:
                    device.emit(uinput.ABS_X, data[TILT], uinput.EV_ABS)
                    device.emit(uinput.ABS_Y, data[WHAMMY], uinput.EV_ABS)
                    device.emit(uinput.ABS_Z, data[PICKUP], uinput.EV_ABS)
                    device.emit(uinput.BTN_SELECT, (data[6] >> 6) & 1)
                device.syn()

    except PermissionError:
        print("Permission error, try running as root.", file=sys.stderr)
    except KeyboardInterrupt:
        pass
