// Generic native display host. No CEF, profiles or business-message
// dependencies.
#include "comfortable/cpu_frame.hpp"
#include <QApplication>
#include <QCloseEvent>
#include <QFontDatabase>
#include <QHideEvent>
#include <QInputMethod>
#include <QInputMethodEvent>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QKeyEvent>
#include <QMouseEvent>
#include <QPainter>
#include <QResizeEvent>
#include <QShowEvent>
#include <QTimer>
#include <QWheelEvent>
#include <QWidget>
#include <algorithm>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <stdexcept>
#include <thread>

namespace {
using Object = QJsonObject;
using Array = QJsonArray;
std::mutex output_lock;
void emit_json(Object o) {
  const auto bytes = QJsonDocument(o).toJson(QJsonDocument::Compact);
  std::lock_guard<std::mutex> guard(output_lock);
  std::cout << bytes.constData() << std::endl;
}
void reply(int id, Object result = {}, const QString &error = {}) {
  Object o{{"id", id}, {"ok", error.isEmpty()}};
  if (error.isEmpty())
    o["result"] = result;
  else
    o["error"] = error;
  emit_json(o);
}
Array modifiers(Qt::KeyboardModifiers m) {
  Array a;
  if (m & Qt::ControlModifier)
    a.append("ctrl");
  if (m & Qt::AltModifier)
    a.append("alt");
  if (m & Qt::ShiftModifier)
    a.append("shift");
  if (m & Qt::MetaModifier)
    a.append("meta");
  return a;
}
QString key_name(QKeyEvent *e) {
  static const std::map<int, QString> special{{Qt::Key_Left, "left"},
                                              {Qt::Key_Right, "right"},
                                              {Qt::Key_Up, "up"},
                                              {Qt::Key_Down, "down"},
                                              {Qt::Key_Return, "enter"},
                                              {Qt::Key_Enter, "enter"},
                                              {Qt::Key_Tab, "tab"},
                                              {Qt::Key_Backtab, "tab"},
                                              {Qt::Key_Escape, "escape"},
                                              {Qt::Key_Backspace, "backspace"},
                                              {Qt::Key_Delete, "delete"},
                                              {Qt::Key_Home, "home"},
                                              {Qt::Key_End, "end"},
                                              {Qt::Key_PageUp, "pageup"},
                                              {Qt::Key_PageDown, "pagedown"},
                                              {Qt::Key_Insert, "insert"},
                                              {Qt::Key_Space, "space"}};
  QString name;
  auto it = special.find(e->key());
  if (it != special.end())
    name = it->second;
  else if (e->key() >= Qt::Key_F1 && e->key() <= Qt::Key_F35)
    name = "f" + QString::number(e->key() - Qt::Key_F1 + 1);
  else
    name = QKeySequence(e->key()).toString().toLower();
  QString prefix;
  if (e->modifiers() & Qt::ControlModifier)
    prefix += "ctrl+";
  if (e->modifiers() & Qt::AltModifier)
    prefix += "alt+";
  if ((e->modifiers() & Qt::ShiftModifier || e->key() == Qt::Key_Backtab) &&
      name.size() != 1)
    prefix += "shift+";
  if (e->modifiers() & Qt::MetaModifier)
    prefix += "meta+";
  return prefix + name;
}
QRectF json_rect(const QJsonValue &v) {
  const auto a = v.toArray();
  if (a.size() != 4)
    throw std::invalid_argument("Expected rectangle [x,y,width,height]");
  return {a[0].toDouble(), a[1].toDouble(), a[2].toDouble(), a[3].toDouble()};
}
QColor color(const QJsonValue &v, QColor fallback) {
  auto a = v.toArray();
  return a.size() == 3 ? QColor(a[0].toInt(), a[1].toInt(), a[2].toInt())
                       : fallback;
}
class Host;
class Hit : public QWidget {
public:
  Hit(Host *host, QString id);
  QString id;
  Object config;
  QRectF full, destination;
  int source_width{}, source_height{};
  void configure(Object o, int cw, int ch);
  Object pointer(QPointF p, QPointF screen, Qt::KeyboardModifiers m) const;
  void send(const QString &kind, Object data = {}) const;
  QVariant inputMethodQuery(Qt::InputMethodQuery q) const override;

protected:
  bool event(QEvent *e) override;
  void paintEvent(QPaintEvent *) override {}
  void mousePressEvent(QMouseEvent *e) override;
  void mouseReleaseEvent(QMouseEvent *e) override;
  void mouseDoubleClickEvent(QMouseEvent *e) override;
  void mouseMoveEvent(QMouseEvent *e) override;
  void wheelEvent(QWheelEvent *e) override;
  void leaveEvent(QEvent *) override { send("mouse_leave"); }
  void focusInEvent(QFocusEvent *) override { send("focus_in"); }
  void focusOutEvent(QFocusEvent *) override { send("focus_out"); }
  void showEvent(QShowEvent *) override { send("show"); }
  void hideEvent(QHideEvent *) override { send("hide"); }
  void inputMethodEvent(QInputMethodEvent *e) override;

private:
  void mouse(QMouseEvent *e, const QString &kind);
  Host *host;
};

class Host : public QWidget {
public:
  Host(bool test_controls) : test_controls(test_controls) {
    setWindowTitle("ComfortableTUI — native window host");
    QFont f = QFontDatabase::systemFont(QFontDatabase::FixedFont);
    f.setPixelSize(18);
    f.setStyleHint(QFont::Monospace);
    setFont(f);
    cell_width = std::max(1, fontMetrics().horizontalAdvance('M'));
    cell_height = std::max(1, fontMetrics().height() + 2);
    resize(100 * cell_width, 30 * cell_height);
    setFocusPolicy(Qt::StrongFocus);
    setMouseTracking(true);
    setAttribute(Qt::WA_InputMethodEnabled);
    QObject::connect(&timer, &QTimer::timeout, this, [this] {
      if (!isVisible() || isMinimized())
        return;
      try {
        bool changed = false;
        for (const auto &[name, source] : sources) {
          auto sequence = source->sequence();
          if (last_sequence[name] != sequence) {
            last_sequence[name] = sequence;
            changed = true;
          }
        }
        if (changed)
          update();
      } catch (const std::exception &e) {
        emit_json({{"event", "error"}, {"message", e.what()}});
      }
    });
    timer.setTimerType(Qt::PreciseTimer);
    timer.start(8);
  }
  Object geometry_info() const {
    return {{"width", width()},
            {"height", height()},
            {"columns", std::max(1, width() / cell_width)},
            {"rows", std::max(1, height() / cell_height)},
            {"cell_width", cell_width},
            {"cell_height", cell_height},
            {"dpi_scale", devicePixelRatioF()}};
  }
  void scene(Object o) {
    const auto new_items = o["items"].toArray();
    if (new_items.size() > 256)
      throw std::invalid_argument("At most 256 canvas views per host");
    std::map<QString, std::shared_ptr<comfortable::CpuFrameChannel>> attached;
    std::set<QString> retained;
    // Attach before replacing the scene. Shared leases never escape the native
    // paint call.
    for (const auto &value : new_items) {
      auto c = value.toObject(), s = c["source"].toObject();
      auto name = s["name"].toString();
      auto current = sources.find(name);
      if (current != sources.end())
        attached[name] = current->second;
      else if (!attached.count(name)) {
        comfortable::CpuFrameInfo info{
            name.toStdString(), static_cast<unsigned>(s["width"].toInt()),
            static_cast<unsigned>(s["height"].toInt()),
            static_cast<unsigned>(s["stride"].toInt()),
            s["pixel_format"].toString().toStdString()};
        attached[name] = std::make_shared<comfortable::CpuFrameChannel>(info);
      }
    }
    for (const auto &value : new_items) {
      auto c = value.toObject();
      auto id = c["id"].toString();
      if (retained.count(id))
        throw std::invalid_argument("Duplicate canvas view id");
      retained.insert(id);
      auto &hit = hits[id];
      if (!hit)
        hit = new Hit(this, id);
      hit->configure(c, cell_width, cell_height);
      hit->raise();
    }
    for (auto it = hits.begin(); it != hits.end();) {
      if (!retained.count(it->first)) {
        delete it->second;
        it = hits.erase(it);
      } else
        ++it;
    }
    sources = std::move(attached);
    for (auto it = last_sequence.begin(); it != last_sequence.end();) {
      if (!sources.count(it->first))
        it = last_sequence.erase(it);
      else
        ++it;
    }
    items = new_items;
    cells = o["cells"].toArray();
    styles = o["styles"].toArray();
    if (cells.size() > 100000 || styles.size() > 4096)
      throw std::invalid_argument("Scene exceeds limits");
    auto focus = o["focus"].toString();
    if (focus != requested_focus) {
      requested_focus = focus;
      if (hits.count(focus) && hits.at(focus)->isVisible())
        hits.at(focus)->setFocus(Qt::OtherFocusReason);
      else
        setFocus(Qt::OtherFocusReason);
    }
    update();
  }
  void control(Object o) {
    const int request = o["id"].toInt();
    const auto op = o["op"].toString();
    try {
      if (op == "scene") {
        scene(o);
        reply(request, geometry_info());
      } else if (op == "metrics") {
        auto r = geometry_info();
        r["paint_calls"] = static_cast<double>(paint_calls);
        r["source_paints"] = static_cast<double>(source_paints);
        r["sources"] = static_cast<int>(sources.size());
        r["views"] = static_cast<int>(hits.size());
        r["visible"] = isVisible();
        r["platform"] = QGuiApplication::platformName();
        reply(request, r);
      } else if (op == "resize") {
        resize(std::clamp(o["width"].toInt(), 100, 8192),
               std::clamp(o["height"].toInt(), 100, 8192));
        reply(request, geometry_info());
      } else if (op == "shutdown") {
        timer.stop();
        for (const auto &[id, hit] : hits)
          delete hit;
        hits.clear();
        items = {};
        sources.clear();
        reply(request);
        qApp->quit();
      } else if (op == "test_input" && test_controls) {
        QWidget *target = this;
        auto id = o["view"].toString();
        if (!id.isEmpty()) {
          if (!hits.count(id))
            throw std::invalid_argument("Unknown test view");
          target = hits.at(id);
        }
        auto kind = o["kind"].toString();
        if (kind == "commit") {
          QInputMethodEvent e;
          e.setCommitString(o["text"].toString());
          QApplication::sendEvent(target, &e);
        } else if (kind == "key") {
          QKeyEvent e(QEvent::KeyPress, o["key"].toInt(), Qt::NoModifier,
                      o["text"].toString());
          QApplication::sendEvent(target, &e);
        } else if (kind == "click") {
          QPointF p(o["x"].toDouble(), o["y"].toDouble());
          auto screen = target->mapToGlobal(p.toPoint());
          QMouseEvent down(QEvent::MouseButtonPress, p, screen, Qt::LeftButton,
                           Qt::LeftButton, Qt::NoModifier);
          QApplication::sendEvent(target, &down);
          QMouseEvent up(QEvent::MouseButtonRelease, p, screen, Qt::LeftButton,
                         Qt::NoButton, Qt::NoModifier);
          QApplication::sendEvent(target, &up);
        } else
          throw std::invalid_argument("Unknown test event");
        reply(request);
      } else if (op == "test_snapshot" && test_controls) {
        // Read only our own backing store, as test evidence; never a frame
        // transport.
        const auto image = grab().toImage();
        std::set<QRgb> colors;
        for (int y = 0; y < image.height(); y += 7)
          for (int x = 0; x < image.width(); x += 7)
            colors.insert(image.pixel(x, y));
        reply(request, {{"sampled_colors", static_cast<int>(colors.size())}});
      } else
        throw std::invalid_argument("Unknown host operation");
    } catch (const std::exception &e) {
      reply(request, {}, QString::fromUtf8(e.what()));
    }
  }
  void tui(const QString &kind, Object data) const {
    emit_json({{"event", "tui_event"}, {"kind", kind}, {"data", data}});
  }
  int cell_width{}, cell_height{};

protected:
  void paintEvent(QPaintEvent *) override {
    QPainter p(this);
    p.fillRect(rect(), QColor(17, 21, 34));
    for (const auto &value : cells) {
      const auto c = value.toArray();
      if (c.size() != 5)
        continue;
      const auto s = styles[c[4].toInt()].toObject();
      auto fg = color(s["fg"], QColor(218, 225, 240)),
           bg = color(s["bg"], QColor(17, 21, 34));
      if (s["reverse"].toBool())
        std::swap(fg, bg);
      QRect r(c[0].toInt() * cell_width, c[1].toInt() * cell_height,
              c[2].toInt() * cell_width, cell_height);
      p.fillRect(r, bg);
      if (c[3].toString().trimmed().isEmpty())
        continue;
      auto f = font();
      f.setBold(s["bold"].toBool());
      f.setItalic(s["italic"].toBool());
      f.setUnderline(s["underline"].toBool());
      p.setFont(f);
      p.setPen(fg);
      p.save();
      p.setClipRect(r);
      p.drawText(r, Qt::AlignLeft | Qt::AlignVCenter, c[3].toString());
      p.restore();
    }
    try {
      // A source mirrored in several views acquires one read lease per paint.
      std::map<QString, comfortable::CpuReadFrame> leases;
      for (const auto &[name, source] : sources)
        leases.emplace(name, source->read());
      for (const auto &value : items) {
        const auto c = value.toObject(), s = c["source"].toObject();
        auto *hit = hits.at(c["id"].toString());
        if (!hit->isVisible())
          continue;
        auto &lease = leases.at(s["name"].toString());
        if (!lease)
          continue;
        QImage::Format format =
            s["pixel_format"].toString() == "rgba8"  ? QImage::Format_RGBA8888
            : s["pixel_format"].toString() == "rgb8" ? QImage::Format_RGB888
            : s["pixel_format"].toString() == "bgra8"
                ? QImage::Format_ARGB32
                : QImage::Format_ARGB32_Premultiplied;
        QImage image(lease.pixels(), s["width"].toInt(), s["height"].toInt(),
                     s["stride"].toInt(), format);
        p.save();
        p.setClipRect(hit->geometry());
        p.setRenderHint(QPainter::SmoothPixmapTransform, c["smooth"].toBool());
        p.drawImage(hit->destination, image);
        p.restore();
        ++source_paints;
      }
      // End QPainter before releasing pixels referenced by QImage wrappers.
      p.end();
    } catch (const std::exception &e) {
      emit_json({{"event", "error"}, {"message", e.what()}});
    }
    ++paint_calls;
  }
  void resizeEvent(QResizeEvent *) override {
    emit_json({{"event", "geometry"}, {"data", geometry_info()}});
  }
  void closeEvent(QCloseEvent *e) override {
    emit_json({{"event", "close_requested"}});
    e->ignore();
    hide();
  }
  bool event(QEvent *e) override {
    if (e->type() == QEvent::KeyPress || e->type() == QEvent::KeyRelease) {
      auto *k = static_cast<QKeyEvent *>(e);
      tui(e->type() == QEvent::KeyPress ? "key_down" : "key_up",
          {{"name", key_name(k)},
           {"text", k->text()},
           {"repeat", k->isAutoRepeat()},
           {"modifiers", modifiers(k->modifiers())}});
      return true;
    }
    return QWidget::event(e);
  }
  void mousePressEvent(QMouseEvent *e) override {
    setFocus();
    mouse(e, "press");
  }
  void mouseReleaseEvent(QMouseEvent *e) override { mouse(e, "release"); }
  void mouseMoveEvent(QMouseEvent *e) override { mouse(e, "move"); }
  void wheelEvent(QWheelEvent *e) override {
    tui("mouse", {{"x", int(e->position().x()) / cell_width},
                  {"y", int(e->position().y()) / cell_height},
                  {"button", e->angleDelta().y() > 0 ? 64 : 65},
                  {"phase", "scroll"}});
  }
  void inputMethodEvent(QInputMethodEvent *e) override {
    tui("text", {{"commit", e->commitString()}});
    e->accept();
  }

private:
  void mouse(QMouseEvent *e, const QString &phase) {
    tui("mouse", {{"x", int(e->position().x()) / cell_width},
                  {"y", int(e->position().y()) / cell_height},
                  {"button", e->button() == Qt::RightButton    ? 2
                             : e->button() == Qt::MiddleButton ? 1
                                                               : 0},
                  {"phase", phase}});
  }
  bool test_controls;
  QTimer timer;
  Array cells, styles, items;
  QString requested_focus;
  std::map<QString, Hit *> hits;
  std::map<QString, std::shared_ptr<comfortable::CpuFrameChannel>> sources;
  std::map<QString, std::uint64_t> last_sequence;
  std::uint64_t paint_calls{}, source_paints{};
};
Hit::Hit(Host *parent, QString value)
    : QWidget(parent), id(std::move(value)), host(parent) {
  setFocusPolicy(Qt::StrongFocus);
  setMouseTracking(true);
  setAttribute(Qt::WA_InputMethodEnabled);
  setAttribute(Qt::WA_NoSystemBackground);
}
void Hit::configure(Object o, int cw, int ch) {
  config = o;
  const auto r = json_rect(o["rect"]), clip = json_rect(o["clip"]);
  full = {r.x() * cw, r.y() * ch, r.width() * cw, r.height() * ch};
  const QRectF clipped(clip.x() * cw, clip.y() * ch, clip.width() * cw,
                       clip.height() * ch);
  setGeometry(full.intersected(clipped).toAlignedRect());
  const auto s = o["source"].toObject();
  source_width = s["width"].toInt();
  source_height = s["height"].toInt();
  auto size = full.size();
  const auto fit = o["fit"].toString();
  if (fit != "stretch") {
    double scale = fit == "pixel"   ? 1.0 / devicePixelRatioF()
                   : fit == "cover" ? std::max(size.width() / source_width,
                                               size.height() / source_height)
                                    : std::min(size.width() / source_width,
                                               size.height() / source_height);
    size = {source_width * scale, source_height * scale};
  }
  destination = {full.x() + (full.width() - size.width()) / 2,
                 full.y() + (full.height() - size.height()) / 2, size.width(),
                 size.height()};
  setEnabled(o["enabled"].toBool(true));
  setAttribute(Qt::WA_TransparentForMouseEvents,
               o["input_mode"].toString() == "passthrough");
  setVisible(!geometry().isEmpty() && o["visible"].toBool(true));
  if (hasFocus())
    QGuiApplication::inputMethod()->update(Qt::ImQueryAll);
}
Object Hit::pointer(QPointF local, QPointF screen,
                    Qt::KeyboardModifiers m) const {
  QPointF point = local + pos();
  const auto source = QPointF(
      (point.x() - destination.x()) * source_width / destination.width(),
      (point.y() - destination.y()) * source_height / destination.height());
  return {{"position", Array{point.x() - full.x(), point.y() - full.y()}},
          {"screen_position", Array{screen.x(), screen.y()}},
          {"source_position", Array{source.x(), source.y()}},
          {"inside_source",
           QRectF(0, 0, source_width, source_height).contains(source)},
          {"terminal_position", Array{int(point.x()) / host->cell_width,
                                      int(point.y()) / host->cell_height}},
          {"modifiers", modifiers(m)}};
}
void Hit::send(const QString &kind, Object data) const {
  emit_json({{"event", "canvas_event"},
             {"view", id},
             {"token", config["token"]},
             {"kind", kind},
             {"data", data}});
}
void Hit::mouse(QMouseEvent *e, const QString &kind) {
  if (kind == "mouse_down")
    setFocus(Qt::MouseFocusReason);
  auto data = pointer(e->position(), e->globalPosition(), e->modifiers());
  data["button"] = int(e->button());
  data["buttons"] = int(e->buttons());
  send(kind, data);
  e->accept();
}
void Hit::mousePressEvent(QMouseEvent *e) { mouse(e, "mouse_down"); }
void Hit::mouseReleaseEvent(QMouseEvent *e) { mouse(e, "mouse_up"); }
void Hit::mouseDoubleClickEvent(QMouseEvent *e) {
  mouse(e, "mouse_double_click");
}
void Hit::mouseMoveEvent(QMouseEvent *e) { mouse(e, "mouse_move"); }
void Hit::wheelEvent(QWheelEvent *e) {
  auto data = pointer(e->position(), e->globalPosition(), e->modifiers());
  data["angle_delta"] = Array{e->angleDelta().x(), e->angleDelta().y()};
  data["pixel_delta"] = Array{e->pixelDelta().x(), e->pixelDelta().y()};
  send("wheel", data);
  e->accept();
}
bool Hit::event(QEvent *e) {
  if (e->type() == QEvent::KeyPress || e->type() == QEvent::KeyRelease) {
    const auto *k = static_cast<QKeyEvent *>(e);
    send(e->type() == QEvent::KeyPress ? "key_down" : "key_up",
         {{"name", key_name(const_cast<QKeyEvent *>(k))},
          {"text", k->text()},
          {"key", k->key()},
          {"scan_code", int(k->nativeScanCode())},
          {"virtual_key", int(k->nativeVirtualKey())},
          {"repeat", k->isAutoRepeat()},
          {"modifiers", modifiers(k->modifiers())}});
    return true;
  }
  return QWidget::event(e);
}
void Hit::inputMethodEvent(QInputMethodEvent *e) {
  Array attrs;
  for (const auto &a : e->attributes())
    attrs.append(Object{
        {"type", int(a.type)}, {"start", a.start}, {"length", a.length}});
  send("ime", {{"commit", e->commitString()},
               {"preedit", e->preeditString()},
               {"replacement_start", e->replacementStart()},
               {"replacement_length", e->replacementLength()},
               {"attributes", attrs}});
  e->accept();
}
QVariant Hit::inputMethodQuery(Qt::InputMethodQuery q) const {
  const auto ime = config["ime"].toObject();
  if (q == Qt::ImCursorRectangle) {
    auto r = json_rect(ime["cursor"]);
    return QRectF(
        destination.x() - x() + r.x() * destination.width() / source_width,
        destination.y() - y() + r.y() * destination.height() / source_height,
        std::max(1.0, r.width() * destination.width() / source_width),
        r.height() * destination.height() / source_height);
  }
  if (q == Qt::ImSurroundingText)
    return ime["text"].toString();
  if (q == Qt::ImCursorPosition)
    return ime["position"].toInt();
  if (q == Qt::ImAnchorPosition)
    return ime["anchor"].toInt();
  if (q == Qt::ImEnabled)
    return isEnabled();
  return QWidget::inputMethodQuery(q);
}
} // namespace

int main(int argc, char **argv) {
  QApplication app(argc, argv);
  if (QGuiApplication::platformName() != "xcb" &&
      !QGuiApplication::platformName().startsWith("wayland")) {
    std::cerr << "Requires actual X11 or Wayland; offscreen/minimal backends "
                 "are not supported"
              << std::endl;
    return 2;
  }
  Host host(app.arguments().contains("--test-controls"));
  host.show();
  auto ready = host.geometry_info();
  ready["event"] = "host_ready";
  ready["protocol"] = 1;
  ready["platform"] = QGuiApplication::platformName();
  ready["mode"] = "window_host";
  ready["frame_backend"] = "native_cpu_qt_raster";
  ready["external_terminal_overlay"] = false;
  emit_json(ready);
  std::thread reader([&host] {
    std::string line;
    while (std::getline(std::cin, line)) {
      if (line.size() > 1024 * 1024) {
        reply(0, {}, "Control message exceeds 1 MiB");
        continue;
      }
      QJsonParseError error;
      const auto doc =
          QJsonDocument::fromJson(QByteArray::fromStdString(line), &error);
      if (error.error != QJsonParseError::NoError || !doc.isObject()) {
        reply(0, {}, "Invalid JSON control message");
        continue;
      }
      const auto o = doc.object();
      QMetaObject::invokeMethod(
          &host, [&host, o] { host.control(o); }, Qt::QueuedConnection);
      if (o["op"].toString() == "shutdown")
        return;
    }
    QMetaObject::invokeMethod(qApp, [] { qApp->quit(); }, Qt::QueuedConnection);
  });
  const int result = app.exec();
  // On window close the Python owner sends shutdown and closes stdin. If the
  // owner dies, pipe EOF also terminates the native event loop and this reader.
  reader.join();
  return result;
}
