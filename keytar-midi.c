#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <alsa/asoundlib.h>
#include <linux/uinput.h>

#define UINPUT_DEV_NAME "Mapped Rock Band 3 Keyboard"
const int keymap[] = {
    BTN_B, 0, BTN_C, 0, BTN_X, BTN_A, 0, BTN_Y, 0, 0, 0, 0,
    BTN_MODE, BTN_SELECT, BTN_START, BTN_TL, BTN_TR,
};

int uinput_init() {
    struct uinput_user_dev usetup;
    int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
    if (fd < 0) return -1;
    ioctl(fd, UI_SET_EVBIT, EV_KEY);
    ioctl(fd, UI_SET_EVBIT, EV_ABS);
    for (int i = 0; i < sizeof(keymap)/sizeof(*keymap); i++) {
        if(keymap[i]) ioctl(fd, UI_SET_KEYBIT, keymap[i]);
    }
    ioctl(fd, UI_SET_ABSBIT, ABS_HAT0X);
    ioctl(fd, UI_SET_ABSBIT, ABS_HAT0Y);
    ioctl(fd, UI_SET_ABSBIT, ABS_X);
    ioctl(fd, UI_SET_ABSBIT, ABS_Y);
    ioctl(fd, UI_SET_ABSBIT, ABS_Z);
    memset(&usetup, 0, sizeof(usetup));
    usetup.absmin[ABS_HAT0X] = -1;
    usetup.absmin[ABS_HAT0Y] = -1;
    usetup.absmax[ABS_HAT0X] = 1;
    usetup.absmax[ABS_HAT0Y] = 1;
    usetup.absmax[ABS_X] = 127;
    usetup.absmax[ABS_Y] = 127;
    usetup.absmin[ABS_Z] = -8192;
    usetup.absmax[ABS_Z] = 8191;
    usetup.id.bustype = BUS_USB;
    usetup.id.vendor = 0x1209; // Generic
    usetup.id.product = 0;
    strcpy(usetup.name, UINPUT_DEV_NAME);
    write(fd, &usetup, sizeof(usetup));
    ioctl(fd, UI_DEV_CREATE);
    return fd;
}
void uinput_emit(int fd, int type, int code, int val) {
   struct input_event ie;
   ie.type = type;
   ie.code = code;
   ie.value = val;
   ie.time.tv_sec = 0;
   ie.time.tv_usec = 0;
   write(fd, &ie, sizeof(ie));
}

void uinput_emit_syn(int fd, int type, int code, int val) {
    uinput_emit(fd, type, code, val);
    uinput_emit(fd, EV_SYN, SYN_REPORT, 0);
}

void uinput_click(int fd, int code) {
    uinput_emit_syn(fd, EV_KEY, code, 1);
    uinput_emit_syn(fd, EV_KEY, code, 0);
}

static void check_snd(const char *msg, int err) {
    if (err < 0) {
        fprintf(stderr, "%s: %s\n", msg, snd_strerror(err));
        exit(EXIT_FAILURE);
    }
}

static snd_seq_t *seq_init(const char *port_name) {
    static snd_seq_t *seq;
    static snd_seq_addr_t port;
    int err;
    err = snd_seq_open(&seq, "default", SND_SEQ_OPEN_DUPLEX, 0);
    check_snd("Cannot open sequencer", err);
    err = snd_seq_set_client_name(seq, "rb3keytar");
    check_snd("Cannot set client name", err);
    err = snd_seq_parse_address(seq, &port, port_name);
    check_snd("Invalid port", err);
    err = snd_seq_create_simple_port(seq, "rb3keytar",
        SND_SEQ_PORT_CAP_WRITE | SND_SEQ_PORT_CAP_SUBS_WRITE,
        SND_SEQ_PORT_TYPE_MIDI_GENERIC | SND_SEQ_PORT_TYPE_APPLICATION);
    check_snd("Cannot create port", err);
    err = snd_seq_connect_from(seq, 0, port.client, port.port);
    check_snd("Cannot connect from port", err);
    return seq;
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Specify alsa midi port as a parameter\n");        
        exit(EXIT_FAILURE);
    }
    snd_seq_t *seq = seq_init(argv[1]);
    int uinput_fd = uinput_init();
    if (uinput_fd < 0) {
        fprintf(stderr, "Cannot create uinput\n");        
        exit(EXIT_FAILURE);
    }
    for (;;) {
        snd_seq_event_t *ev;
        if (snd_seq_event_input(seq, &ev) < 0)
            break;
        if (ev->type == SND_SEQ_EVENT_NOTEOFF || ev->type == SND_SEQ_EVENT_NOTEON) {
            int on = ev->type == SND_SEQ_EVENT_NOTEON;
            int note = ev->data.note.note - 48;
            if (note == 1 || note == 3) {
                uinput_emit_syn(uinput_fd, EV_ABS, ABS_HAT0Y, on ? note == 1 ? -1 : 1 : 0);
            } else if (note >= 0 && note < 12) {
                uinput_emit_syn(uinput_fd, EV_KEY, keymap[note], on);
            }
        } else if (ev->type == SND_SEQ_EVENT_START) {
            uinput_click(uinput_fd, BTN_START);
        } else if (ev->type == SND_SEQ_EVENT_CONTINUE) {
            uinput_click(uinput_fd, BTN_MODE);
        } else if (ev->type == SND_SEQ_EVENT_STOP) {
            uinput_click(uinput_fd, BTN_SELECT);
        } else if (ev->type == SND_SEQ_EVENT_PGMCHANGE) {
            uinput_emit_syn(uinput_fd, EV_ABS, ABS_Y, ev->data.control.value);
        } else if (ev->type == SND_SEQ_EVENT_CONTROLLER) {
            if (ev->data.control.param == 1) {
                uinput_emit_syn(uinput_fd, EV_ABS, ABS_X, ev->data.control.value);
            } else {
                uinput_emit_syn(uinput_fd, EV_KEY, ev->data.control.param == 64 ? BTN_TL : BTN_TR, ev->data.control.value > 0);
            }
        } else if (ev->type == SND_SEQ_EVENT_PITCHBEND) {
            uinput_emit_syn(uinput_fd, EV_ABS, ABS_Z, ev->data.control.value);
        }
    }
    return 0;
}
