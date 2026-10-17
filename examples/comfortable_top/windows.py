"""One Toolhelp snapshot for parent/thread metadata; no per-PID O(N) scan."""
import ctypes
from ctypes import wintypes
from types import SimpleNamespace


def process_relations():
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        fn = getattr(kernel, name)
        fn.argtypes, fn.restype = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found, result = kernel.Process32FirstW(handle, ctypes.byref(entry)), {}
        while found:
            result[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.cntThreads, entry.szExeFile)
            found = kernel.Process32NextW(handle, ctypes.byref(entry))
        error = ctypes.get_last_error()
        if error != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(error)
        return result
    finally:
        kernel.CloseHandle(handle)


def resident_memory():
    """Documented SystemProcessInformation prefix, one query for all RSS.

    Reserved fields remain opaque. An unavailable API returns no readings,
    rather than making one full system query per protected process.
    """
    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]
    class ProcessInfo(ctypes.Structure):
        _fields_ = [("NextEntryOffset", wintypes.ULONG), ("NumberOfThreads", wintypes.ULONG),
            ("Reserved1", ctypes.c_byte * 48), ("ImageName", UnicodeString), ("BasePriority", wintypes.LONG),
            ("UniqueProcessId", wintypes.HANDLE), ("Reserved2", ctypes.c_void_p),
            ("HandleCount", wintypes.ULONG), ("SessionId", wintypes.ULONG), ("Reserved3", ctypes.c_void_p),
            ("PeakVirtualSize", ctypes.c_size_t), ("VirtualSize", ctypes.c_size_t), ("Reserved4", wintypes.ULONG),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t)]
    try:
        query = ctypes.WinDLL("ntdll").NtQuerySystemInformation
        query.argtypes = [ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
        query.restype = ctypes.c_long
        size = 256 * 1024
        for _ in range(8):
            buffer = ctypes.create_string_buffer(size)
            needed = wintypes.ULONG()
            status = query(5, buffer, size, ctypes.byref(needed))
            if status == 0:
                break
            if status & 0xffffffff != 0xc0000004:
                return {}
            size = max(size * 2, needed.value + 65536)
            if size > 128 * 1024 * 1024:
                return {}
        else:
            return {}
        offset, result = 0, {}
        while offset + ctypes.sizeof(ProcessInfo) <= size:
            entry = ProcessInfo.from_buffer(buffer, offset)
            result[entry.UniqueProcessId or 0] = entry.WorkingSetSize
            if not entry.NextEntryOffset:
                break
            if entry.NextEntryOffset < ctypes.sizeof(ProcessInfo):
                return {}
            offset += entry.NextEntryOffset
        return result
    except (OSError, AttributeError):
        return {}


def process_records():
    """Avoid psutil's whole-system fallback for each protected create_time.

    Processes without readable creation time are reported unavailable, instead
    of assigning a PID-only identity that could select an unrelated process.
    """
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    memory = resident_memory()
    for pid, (parent, threads, name) in process_relations().items():
        if pid == 0:
            continue
        info = dict(pid=pid, ppid=parent, name=name, status="alive", num_threads=threads,
                    create_time=None, cpu_times=None, memory_info=None)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
                if kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(system), ctypes.byref(user)):
                    def ticks(value):
                        return (value.dwHighDateTime << 32) | value.dwLowDateTime
                    info["create_time"] = (ticks(created) - 116444736000000000) / 10000000
                    info["cpu_times"] = SimpleNamespace(user=ticks(user) / 10000000, system=ticks(system) / 10000000)
                    rss = memory.get(pid)
                    info["memory_info"] = SimpleNamespace(rss=rss) if rss is not None else None
            finally:
                kernel.CloseHandle(handle)
        yield info
