# 通用 C++ CPU 共享帧

`comfortable/cpu_frame.hpp` 与 `comfortable_cpu_frames` 是独立的图像传输库，
C++17、Windows/POSIX，无 Qt/CEF/Python 头文件依赖。它附加到 Python FrameBuffer
分配的 CTUIFR01 映射，提供原生生产者和消费者；不把 URL、Cookie 或主题放入帧协议。
GPU SDK 仍使用原有 FrameProvider/FrameConsumer，见 [GPU SDK](native-gpu.md)。

```cmake
find_package(ComfortableFrames CONFIG REQUIRED)
target_link_libraries(my_renderer PRIVATE Comfortable::comfortable_cpu_frames)
```

```cpp
#include <comfortable/cpu_frame.hpp>
comfortable::CpuFrameInfo info{name, width, height, stride, "bgra8_premultiplied"};
comfortable::CpuFrameChannel channel(info);
channel.submit(pixel_data, source_stride); // 返回后生产者可复用源指针
{
    auto frame = channel.read();
    if (frame) {
        consume(frame.pixels(), frame.sequence(), frame.timestamp());
    }
} // RAII 释放读槽；不得继续使用 pixels 指针
```

| API | 行为 |
| --- | --- |
| CpuFrameInfo | name、width、height、stride、pixel_format；描述符由握手传递，不含裸跨进程指针 |
| CpuFrameChannel(info) | 验证名称/尺寸/映射/header，打开现有映射；不会分配新共享内存 |
| info() | 返回附加描述符引用，生命周期为该 channel |
| submit(pixels, source_stride, timestamp=0) | 同步复制整帧；timestamp=0 使用单调时钟秒；不能与自己持有的同通道读租约冲突使用 |
| sequence() | 读取当前已发布序号；尚未发布为 0 |
| read() | 串行消费者读锁，返回可移动、不复制的 CpuReadFrame；未发布时 bool 为 false |
| CpuReadFrame | pixels() 只读指针、sequence()、timestamp()；租约持有通道映射引用 |
| monotonic_seconds() | Windows QPC / Linux CLOCK_MONOTONIC，单位秒，与同机 Python perf_counter 对齐 |

格式支持 rgba8、bgra8、bgra8_premultiplied、rgb8，stride 是包含行填充的字节数，
须为 4 的倍数。submit 要求源 stride 至少为描述符 stride，每行复制后者字节数。
宽高 1..16384，总映射不超过 512 MiB。名称为 ctui_ 开头的受限字母数字/下划线。
构造、非法描述符、映射缺失/header 不兼容和 2 秒锁超时抛标准 C++ 异常。
建议使用 BGRA8 premultiplied：透明内容必须预乘；浏览器输出已符合。

映射开头为 32 字节的 magic/published/reading/sequence/timestamp，像素偏移 64，
后接 3 个 stride*height 槽；写端避开当前发布和当前读取槽。多生产者以 writer 锁
串行，多消费者以 reader 锁串行；slow reader 不会让写端覆写正在使用的像素。
序号可跳过，语义是 latest mailbox，不是逐帧可靠队列。
Windows 使用命名 mutex，POSIX 使用共享内存与临时目录 flock 文件；两个进程必须
使用相同的临时目录。锁和映射名称/元数据继承 FrameBuffer 的 v1 ABI。

Python 必须保持 FrameBuffer owner 活着，直到写端停止且全部原生消费者解除附加。
停止顺序是显示消费者 → 生产者/服务 → FrameBuffer owner。更换尺寸先建立新映射、
握手描述符，等待原生消费者确认，再回收旧 owner；不能原地扩大映射。
一个原生进程给同源的多个视图读取时应共用同一个租约，避免同进程重复加 reader 锁。

这是 CPU 传输：存在整帧复制、内存带宽及显示端上传/缩放成本，不称为 GPU 共享。
当前消费者以有界定时检查 sequence，不承诺跨平台就绪事件通知。真实 CEF/Qt 示例
验证 Python 不进行逐帧像素复制；不应把这些示例的性能外推到 4K 或任意帧率。
