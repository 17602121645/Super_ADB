# AOSP 9.0 ADB 源码

本仓库收录自 AOSP 9.0 的 ADB 模块完整源码，旨在系统性解析其通信机制与实现细节，在无 adb client 的环境中，直接与设备端
`adbd` 守护进程进行通信所需的协议格式、数据交互流程及相关实现提供参考与支持。

[protocol.txt](protocol.txt)

[SERVICES.TXT](SERVICES.TXT)

---

根据对 ADB 源码的分析，adb client 与 adbd 通信时，实际发送的服务请求格式如下：

大多数请求在 [SERVICES.TXT](SERVICES.TXT) 中有文档说明：

```
shell:command arg1 arg2 ...
    Run 'command arg1 arg2 ...' in a shell on the device, and return
    its output and error streams. Note that arguments must be separated
    by spaces. If an argument contains a space, it must be quoted with
    double-quotes. Arguments cannot contain double quotes or things
    will go very wrong.

    Note that this is the non-interactive version of "adb shell"

shell:
    Start an interactive shell session on the device. Redirect
    stdin/stdout/stderr as appropriate. Note that the ADB server uses
    this to implement "adb shell", but will also cook the input before
    sending it to the device (see interactive_shell() in commandline.c)

remount:
    Ask adbd to remount the device's filesystem in read-write mode,
    instead of read-only. This is usually necessary before performing
    an "adb sync" or "adb push" request.

    This request may not succeed on certain builds which do not allow
    that.

```

部分请求仅可从源码 [services.cpp](services.cpp) 中获取：

- `adb disable-verity` 对应：`disable-verity:`
