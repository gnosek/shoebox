from _ctypes import POINTER
from ctypes import CDLL, c_void_p, c_int, c_long, c_char_p, cast

libcap = CDLL('libcap.so.2')

# linux/capability.h
cap_value_t = c_int
CAP_CHOWN = 0
CAP_DAC_OVERRIDE = 1
CAP_DAC_READ_SEARCH = 2
CAP_FOWNER = 3
CAP_FSETID = 4
CAP_KILL = 5
CAP_SETGID = 6
CAP_SETUID = 7
CAP_SETPCAP = 8
CAP_LINUX_IMMUTABLE = 9
CAP_NET_BIND_SERVICE = 10
CAP_NET_BROADCAST = 11
CAP_NET_ADMIN = 12
CAP_NET_RAW = 13
CAP_IPC_LOCK = 14
CAP_IPC_OWNER = 15
CAP_SYS_MODULE = 16
CAP_SYS_RAWIO = 17
CAP_SYS_CHROOT = 18
CAP_SYS_PTRACE = 19
CAP_SYS_PACCT = 20
CAP_SYS_ADMIN = 21
CAP_SYS_BOOT = 22
CAP_SYS_NICE = 23
CAP_SYS_RESOURCE = 24
CAP_SYS_TIME = 25
CAP_SYS_TTY_CONFIG = 26
CAP_MKNOD = 27
CAP_LEASE = 28
CAP_AUDIT_WRITE = 29
CAP_AUDIT_CONTROL = 30
CAP_SETFCAP = 31
CAP_MAC_OVERRIDE = 32
CAP_MAC_ADMIN = 33
CAP_SYSLOG = 34
CAP_WAKE_ALARM = 35
CAP_BLOCK_SUSPEND = 36

try:
    with open('/proc/sys/kernel/cap_last_cap') as fp:
        CAP_LAST_CAP = int(fp.read())
except (IOError, ValueError):
    CAP_LAST_CAP = CAP_BLOCK_SUSPEND

DEFAULT_CAPS = (
    CAP_CHOWN,
    CAP_DAC_OVERRIDE,
    # CAP_DAC_READ_SEARCH,
    CAP_FOWNER,

    CAP_FSETID,
    CAP_KILL,
    CAP_SETUID,
    CAP_SETGID,

    CAP_SETPCAP,
    # CAP_LINUX_IMMUTABLE,
    CAP_NET_BIND_SERVICE,
    # CAP_NET_BROADCAST,

    # CAP_NET_ADMIN,
    CAP_NET_RAW,
    # CAP_IPC_LOCK,
    # CAP_IPC_OWNER,

    # CAP_SYS_MODULE,
    # CAP_SYS_RAWIO,
    CAP_SYS_CHROOT,
    # CAP_SYS_PTRACE,

    # CAP_SYS_PACCT,
    # CAP_SYS_ADMIN,
    # CAP_SYS_BOOT,
    # CAP_SYS_NICE,

    # CAP_SYS_RESOURCE,
    # CAP_SYS_TIME,
    # CAP_SYS_TTY_CONFIG,
    CAP_MKNOD,

    # CAP_LEASE,
    CAP_AUDIT_WRITE,
    # CAP_AUDIT_CONTROL,
    CAP_SETFCAP,

    # CAP_MAC_OVERRIDE,
    # CAP_MAC_ADMIN,
    # CAP_SYSLOG,
    # CAP_WAKE_ALARM,

    # CAP_BLOCK_SUSPEND,
)

# sys/capability.h
cap_flag_t = c_int
CAP_EFFECTIVE = 0
CAP_PERMITTED = 1
CAP_INHERITABLE = 2

cap_flag_value_t = c_int
CAP_CLEAR = 0
CAP_SET = 1


def cap_errcheck(result, func, args):
    if result:
        raise OSError('Call to {0}{1!r} failed'.format(func.__name__, args))
    return args

def cap_get_proc_errcheck(result, func, args):
    if not result:
        raise OSError('Call to {0}{1!r} failed'.format(func.__name__, args))
    return args

cap_t = c_void_p  # opaque structure

cap_get_proc = libcap.cap_get_proc
cap_get_proc.errcheck = cap_get_proc_errcheck
cap_get_proc.restype = cap_t

cap_clear = libcap.cap_clear
cap_clear.argtypes = [cap_t]
cap_clear.errcheck = cap_errcheck

cap_set_flag = libcap.cap_set_flag
cap_set_flag.argtypes = [cap_t, cap_flag_t, c_int, POINTER(cap_value_t), cap_flag_value_t]
cap_clear.errcheck = cap_errcheck

cap_set_proc = libcap.cap_set_proc
cap_set_proc.argtypes = [cap_t]
cap_set_proc.errcheck = cap_errcheck

cap_free = libcap.cap_free
cap_free.argtypes = [cap_t]
cap_free.errcheck = cap_errcheck

cap_drop_bound = libcap.cap_drop_bound
cap_drop_bound.argtypes = [cap_value_t]
cap_drop_bound.errcheck = cap_errcheck

cap_to_text = libcap.cap_to_text
cap_to_text.argtypes = [cap_t, POINTER(c_long)]
cap_to_text.restype = c_void_p


def dump_caps():
    proc_caps = cap_get_proc()
    cap_text = cap_to_text(proc_caps, None)
    cap_str = str(cast(cap_text, c_char_p).value)
    cap_free(cap_text)
    cap_free(proc_caps)
    return cap_str


def drop_caps(cap_keep=DEFAULT_CAPS):
    ncaps = len(cap_keep)
    # noinspection PyCallingNonCallable
    cap_flags = (cap_value_t * ncaps)(*cap_keep)

    cap_set = frozenset(cap_keep)
    for cap in range(0, CAP_LAST_CAP+1):
        if cap not in cap_set:
            cap_drop_bound(cap)

    proc_caps = cap_get_proc()
    cap_clear(proc_caps)
    cap_set_flag(proc_caps, CAP_INHERITABLE, ncaps, cap_flags, CAP_SET)
    cap_set_flag(proc_caps, CAP_EFFECTIVE, ncaps, cap_flags, CAP_SET)
    cap_set_flag(proc_caps, CAP_PERMITTED, ncaps, cap_flags, CAP_SET)
    cap_set_proc(proc_caps)
