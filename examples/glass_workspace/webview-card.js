const name = new URLSearchParams(location.search).get('name') || 'Browser card';
document.getElementById('name').textContent = name;
let received = null, clicks = 0;
function describe(type, nonce) {
  const bounds = id => {
    const r = document.getElementById(id).getBoundingClientRect();
    return {x:r.x, y:r.y, width:r.width, height:r.height};
  };
  comfortable.publish('glass.telemetry', {type, nonce, name, clicks, received,
    value:document.getElementById('entry').value, focus:document.activeElement.id,
    viewport:[innerWidth, innerHeight], entry:bounds('entry'), send:bounds('send')});
}
const timer = setInterval(() => {
  if (!window.comfortable) return;
  clearInterval(timer);
  comfortable.subscribe('glass.messages', message => {
    received = message.payload;
    document.getElementById('received').textContent = `${received.name}: ${received.text}`;
  });
  comfortable.subscribe('glass.commands', message => {
    if (message.payload.op === 'inspect') describe('inspect', message.payload.nonce);
  });
  document.getElementById('form').onsubmit = event => {
    event.preventDefault();
    clicks++;
    comfortable.publish('glass.messages', {name, text:document.getElementById('entry').value});
    describe('sent', null);
  };
  describe('ready', null);
}, 50);
