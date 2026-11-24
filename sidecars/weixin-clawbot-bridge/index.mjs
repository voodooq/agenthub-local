/**
 * AgentHub 个人微信 ClawBot 原生官方协议桥接进程（直连腾讯 ilinkai.weixin.qq.com）
 *
 * 完整闭环：
 * 1. POST /qr/start  -> 调用腾讯官方 GET /ilink/bot/get_bot_qrcode?bot_type=3 获取真微信扫码 URL
 *                       (https://liteapp.weixin.qq.com/...) 并后台自动轮询扫码状态；
 * 2. 手机微信扫码确认 -> 自动拿到 bot_token / baseurl / ilink_user_id，回调 AgentHub 完成自动绑定，
 *                       并立即启动 POST /ilink/bot/getupdates 长轮询收消息；
 * 3. 收到微信消息     -> 保存该对话的 context_token，转发给 AgentHub /api/v1/dialogue-wiki/ingress；
 * 4. POST /send       -> 携带 context_token 调用 POST /ilink/bot/sendmessage 把专家回复推回手机微信。
 */
import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const PORT = Number(process.env.CLAWBOT_BRIDGE_PORT || 18791);
const DEFAULT_ILINK_BASE = 'https://ilinkai.weixin.qq.com';
const DEFAULT_ILINK_CDN_BASE = (process.env.ILINK_CDN_BASE_URL || 'https://novac2c.cdn.weixin.qq.com/c2c').replace(/\/+$/, '');
const BRIDGE_SECRET = (process.env.CLAWBOT_BRIDGE_SECRET || '').trim();
const LEGACY_SESSION_FILE = path.join(os.homedir(), '.agenthub', 'weixin_clawbot_session.json');
const SESSION_FILE = (process.env.CLAWBOT_SESSION_FILE || '').trim() || LEGACY_SESSION_FILE;

function savePersistedSession() {
  try {
    fs.mkdirSync(path.dirname(SESSION_FILE), { recursive: true });
    fs.writeFileSync(
      SESSION_FILE,
      JSON.stringify(
        {
          bot_token: state.bot_token,
          ilink_base_url: state.ilink_base_url,
          wxid: state.wxid,
          account_label: state.account_label,
          ingress_url: state.ingress_url,
          access_token: state.access_token,
          get_updates_buf: state.get_updates_buf,
          context_tokens: Object.fromEntries(state.context_tokens.entries()),
        },
        null,
        2
      ),
      'utf8'
    );
  } catch {
    // 忽略落盘异常
  }
}

function loadPersistedSession() {
  try {
    let targetFile = SESSION_FILE;
    if (!fs.existsSync(targetFile) && targetFile !== LEGACY_SESSION_FILE && fs.existsSync(LEGACY_SESSION_FILE)) {
      targetFile = LEGACY_SESSION_FILE;
    }
    if (!fs.existsSync(targetFile)) return;
    const saved = JSON.parse(fs.readFileSync(targetFile, 'utf8'));
    if (saved && saved.bot_token) {
      state.bot_token = String(saved.bot_token);
      state.ilink_base_url = String(saved.ilink_base_url || DEFAULT_ILINK_BASE);
      state.wxid = String(saved.wxid || '');
      state.account_label = String(saved.account_label || state.wxid);
      if (saved.ingress_url) state.ingress_url = String(saved.ingress_url);
      if (saved.access_token) state.access_token = String(saved.access_token);
      if (saved.get_updates_buf) state.get_updates_buf = String(saved.get_updates_buf);
      if (saved.context_tokens && typeof saved.context_tokens === 'object') {
        for (const [k, v] of Object.entries(saved.context_tokens)) {
          if (k && v) state.context_tokens.set(String(k), String(v));
        }
      }
      state.logged_in = true;
      state.qr_status = 'confirmed';
    }
    // 环境变量中携带最新的服务端回环地址与临时 Access Token 时，覆盖旧持久化值
    const envIngress = (process.env.AGENTHUB_INGRESS_URL || '').trim();
    const envToken = (process.env.AGENTHUB_ACCESS_TOKEN || '').trim();
    let changed = targetFile !== SESSION_FILE;
    if (envIngress && envIngress !== state.ingress_url) {
      state.ingress_url = envIngress;
      changed = true;
    }
    if (envToken && envToken !== state.access_token) {
      state.access_token = envToken;
      changed = true;
    }
    if (changed && state.bot_token) {
      savePersistedSession();
    }
  } catch {
    // 忽略读取异常
  }
}

const state = {
  logged_in: false,
  qr_status: 'idle', // idle | wait | scaned | confirmed | expired
  account_label: '',
  wxid: '',
  bot_token: '',
  ilink_base_url: DEFAULT_ILINK_BASE,
  qr_code_id: '',
  qr_url: '',
  get_updates_buf: '',
  active_session: null,
  ingress_url: (process.env.AGENTHUB_INGRESS_URL || '').trim(),
  access_token: '',
  rx_count: 0,
  last_rx_text: '',
  last_forward_status: 0,
  context_tokens: new Map(), // user_id -> context_token
  typing_tickets: new Map(), // user_id -> { ticket, expiresAt }
  polling_qr: false,
  polling_updates: false,
};

function randomWechatUin() {
  const num = crypto.randomBytes(4).readUInt32BE(0);
  return Buffer.from(String(num), 'utf8').toString('base64');
}

function buildIlinkHeaders() {
  const headers = {
    'Content-Type': 'application/json',
    'X-WECHAT-UIN': randomWechatUin(),
  };
  if (state.bot_token) {
    headers['AuthorizationType'] = 'ilink_bot_token';
    headers['Authorization'] = `Bearer ${state.bot_token}`;
  }
  return headers;
}

function readJsonBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'));
      } catch {
        resolve({});
      }
    });
  });
}

function sendJson(res, statusCode, data) {
  res.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}

function parseIlinkAesKey(rawKey) {
  const s = String(rawKey || '').trim();
  if (!s) return null;
  if (/^[0-9a-fA-F]{32}$/.test(s)) {
    return Buffer.from(s, 'hex');
  }
  try {
    const b1 = Buffer.from(s, 'base64');
    if (b1.length === 16) return b1;
    if (b1.length === 32) {
      const ascii = b1.toString('utf8');
      if (/^[0-9a-fA-F]{32}$/.test(ascii)) {
        return Buffer.from(ascii, 'hex');
      }
    }
  } catch {
    // 忽略非法 Base64
  }
  return null;
}

function detectMediaMimeAndExt(buf, fallbackMime, fallbackName) {
  if (!Buffer.isBuffer(buf) || buf.length < 4) {
    return { mime: fallbackMime, name: fallbackName };
  }
  const head = buf.subarray(0, Math.min(buf.length, 16));
  const headAscii = head.toString('latin1');
  let mime = fallbackMime;
  let ext = '';
  if (headAscii.includes('#!SILK_V3')) {
    mime = 'audio/silk';
    ext = '.silk';
  } else if (headAscii.startsWith('RIFF')) {
    mime = 'audio/wav';
    ext = '.wav';
  } else if (headAscii.startsWith('#!AMR')) {
    mime = 'audio/amr';
    ext = '.amr';
  } else if (headAscii.startsWith('OggS')) {
    mime = 'audio/ogg';
    ext = '.ogg';
  } else if (headAscii.startsWith('ID3') || (buf[0] === 0xff && (buf[1] & 0xe0) === 0xe0)) {
    mime = 'audio/mpeg';
    ext = '.mp3';
  } else if (buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4e && buf[3] === 0x47) {
    mime = 'image/png';
    ext = '.png';
  } else if (buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff) {
    mime = 'image/jpeg';
    ext = '.jpg';
  }
  let name = fallbackName || 'media';
  if (ext) {
    const parsed = path.parse(name);
    if (!parsed.ext || parsed.ext.toLowerCase() !== ext) {
      name = `${parsed.name || 'wechat_media'}${ext}`;
    }
  }
  return { mime, name };
}

async function downloadAndDecryptIlinkMedia(mediaObj, fallbackAesKey = '', directUrl = '') {
  const eqp = String(
    mediaObj?.encrypt_query_param || mediaObj?.encrypted_query_param || ''
  ).trim();
  let downloadUrl = '';
  if (eqp) {
    downloadUrl = `${DEFAULT_ILINK_CDN_BASE}/download?encrypted_query_param=${encodeURIComponent(eqp)}`;
  } else {
    downloadUrl = String(directUrl || mediaObj?.url || mediaObj?.cdn_url || '').trim();
  }
  if (!downloadUrl) return null;

  try {
    const resp = await fetch(downloadUrl, {
      method: 'GET',
      headers: buildIlinkHeaders(),
      signal: AbortSignal.timeout(20000),
    });
    if (!resp.ok) {
      console.error(`[WeixinBridge] 下载 CDN 媒体失败 HTTP ${resp.status}`);
      return null;
    }
    const rawBuf = Buffer.from(await resp.arrayBuffer());
    if (!rawBuf.length) return null;

    const rawKey = mediaObj?.aes_key || mediaObj?.aeskey || fallbackAesKey || '';
    const keyBuf = parseIlinkAesKey(rawKey);
    let plainBuf = rawBuf;
    if (keyBuf) {
      try {
        const decipher = crypto.createDecipheriv('aes-128-ecb', keyBuf, null);
        plainBuf = Buffer.concat([decipher.update(rawBuf), decipher.final()]);
      } catch {
        const decipherRaw = crypto.createDecipheriv('aes-128-ecb', keyBuf, null);
        decipherRaw.setAutoPadding(false);
        plainBuf = Buffer.concat([decipherRaw.update(rawBuf), decipherRaw.final()]);
      }
    }
    return {
      buffer: plainBuf,
      base64: plainBuf.toString('base64'),
    };
  } catch (err) {
    console.error('[WeixinBridge] 下载或解密媒体异常:', err?.message || err);
    return null;
  }
}

function extractMessagePayloadFromIlinkMsg(msg) {
  const result = {
    text: '',
    msg_type: 'text',
    attachments: [],
    quote_text: '',
    has_media: false,
  };
  if (!msg) return result;

  if (typeof msg.text === 'string' && msg.text.trim()) {
    result.text = msg.text.trim();
  }
  const refObj = msg.ref_msg || msg.quote_msg;
  if (refObj) {
    result.quote_text = String(
      refObj.content || refObj.title || refObj.text || refObj?.text_item?.text || ''
    ).trim();
  }

  const items = Array.isArray(msg.item_list) ? msg.item_list : [];
  const textParts = result.text ? [result.text] : [];

  for (const item of items) {
    if (!item) continue;
    if (item.ref_msg && !result.quote_text) {
      result.quote_text = String(item.ref_msg.content || item.ref_msg.title || '').trim();
    }
    if (item.type === 1 && item.text_item?.text) {
      textParts.push(String(item.text_item.text).trim());
    } else if (typeof item.text === 'string' && item.text.trim()) {
      textParts.push(item.text.trim());
    } else if (item.type === 2 || item.image_item) {
      const img = item.image_item || item;
      const imgUrl = String(img.url || img.pic_url || img.cdn_url || img.aeskey_url || '').trim();
      result.attachments.push({
        kind: 'image',
        mime_type: 'image/jpeg',
        name: String(img.file_name || img.name || 'wechat_image.jpg'),
        url: imgUrl,
        base64: String(img.base64 || img.data || '').trim(),
      });
      result.msg_type = 'image';
      result.has_media = true;
    } else if (item.type === 3 || item.voice_item) {
      const vc = item.voice_item || item;
      const voiceUrl = String(vc.url || vc.voice_url || vc.cdn_url || '').trim();
      const voiceText = String(vc.text || vc.asr_text || vc.transcript || '').trim();
      result.attachments.push({
        kind: 'voice',
        mime_type: String(vc.mime_type || 'audio/silk'),
        name: String(vc.file_name || 'wechat_voice.silk'),
        url: voiceUrl,
        base64: String(vc.base64 || vc.data || '').trim(),
        text: voiceText,
      });
      if (voiceText) textParts.push(voiceText);
      result.msg_type = 'voice';
      result.has_media = true;
    } else if (item.type === 4 || item.file_item) {
      const fl = item.file_item || item;
      result.attachments.push({
        kind: 'file',
        mime_type: String(fl.mime_type || 'application/octet-stream'),
        name: String(fl.file_name || fl.name || 'wechat_file'),
        url: String(fl.url || fl.cdn_url || '').trim(),
      });
      result.msg_type = 'file';
      result.has_media = true;
    }
  }

  result.text = textParts.filter(Boolean).join('\n').trim();
  return result;
}

async function extractAndHydrateIlinkMsg(msg) {
  const payload = extractMessagePayloadFromIlinkMsg(msg);
  if (!msg || !payload.has_media || payload.attachments.length === 0) {
    return payload;
  }

  const items = Array.isArray(msg.item_list) ? msg.item_list : [];
  const mediaSources = [];
  for (const item of items) {
    if (!item) continue;
    if (item.type === 2 || item.image_item) {
      const img = item.image_item || item;
      mediaSources.push({
        mediaObj: img.media || img,
        fallbackAesKey: String(img.aeskey || img.aes_key || ''),
        directUrl: String(img.url || img.pic_url || img.cdn_url || img.aeskey_url || ''),
      });
    } else if (item.type === 3 || item.voice_item) {
      const vc = item.voice_item || item;
      mediaSources.push({
        mediaObj: vc.media || vc,
        fallbackAesKey: String(vc.aeskey || vc.aes_key || ''),
        directUrl: String(vc.url || vc.voice_url || vc.cdn_url || ''),
      });
    } else if (item.type === 4 || item.file_item) {
      mediaSources.push(null);
    }
  }

  for (let i = 0; i < payload.attachments.length; i += 1) {
    const att = payload.attachments[i];
    const src = mediaSources[i];
    if (!att || !src) continue;
    if (att.kind === 'image' && !att.base64) {
      const dl = await downloadAndDecryptIlinkMedia(src.mediaObj, src.fallbackAesKey, src.directUrl);
      if (dl) {
        att.base64 = dl.base64;
        const meta = detectMediaMimeAndExt(dl.buffer, att.mime_type, att.name);
        att.mime_type = meta.mime;
        att.name = meta.name;
      }
    } else if (att.kind === 'voice' && !att.text && !att.base64) {
      const dl = await downloadAndDecryptIlinkMedia(src.mediaObj, src.fallbackAesKey, src.directUrl);
      if (dl) {
        att.base64 = dl.base64;
        const meta = detectMediaMimeAndExt(dl.buffer, att.mime_type, att.name);
        att.mime_type = meta.mime;
        att.name = meta.name;
      }
    }
  }

  const hasText = Boolean(payload.text);
  const b64Sizes = payload.attachments.map((a) => (a.base64 ? a.base64.length : 0));
  console.log(
    `[WeixinBridge] 收到媒体消息 type=${payload.msg_type} count=${payload.attachments.length} has_text=${hasText} b64_len=${b64Sizes.join(',')}`
  );
  return payload;
}

function extractTextFromIlinkMsg(msg) {
  return extractMessagePayloadFromIlinkMsg(msg).text;
}

async function getTypingTicket(userId) {
  if (!state.bot_token || !userId) return '';
  const cached = state.typing_tickets.get(userId);
  if (cached && cached.expiresAt > Date.now() + 60_000) {
    return cached.ticket;
  }
  try {
    const baseUrl = (state.ilink_base_url || DEFAULT_ILINK_BASE).replace(/\/+$/, '');
    const ctxToken = state.context_tokens.get(userId) || '';
    const resp = await fetch(`${baseUrl}/ilink/bot/getconfig`, {
      method: 'POST',
      headers: buildIlinkHeaders(),
      body: JSON.stringify({
        ilink_user_id: userId,
        context_token: ctxToken,
        base_info: { channel_version: '2.0.1' },
      }),
    });
    if (!resp.ok) return '';
    const data = await resp.json();
    const ticket = String(data.typing_ticket || '').trim();
    if (ticket) {
      state.typing_tickets.set(userId, { ticket, expiresAt: Date.now() + 12 * 3600 * 1000 });
    }
    return ticket;
  } catch {
    return '';
  }
}

async function sendIlinkTyping(toUserId, status = 1) {
  if (!state.bot_token || !toUserId) return { ok: false };
  try {
    const ticket = await getTypingTicket(toUserId);
    if (!ticket) return { ok: false, error: 'no_typing_ticket' };
    const baseUrl = (state.ilink_base_url || DEFAULT_ILINK_BASE).replace(/\/+$/, '');
    const resp = await fetch(`${baseUrl}/ilink/bot/sendtyping`, {
      method: 'POST',
      headers: buildIlinkHeaders(),
      body: JSON.stringify({
        ilink_user_id: toUserId,
        to_user_id: toUserId,
        typing_ticket: ticket,
        status: Number(status) === 2 ? 2 : 1,
        base_info: { channel_version: '2.0.1' },
      }),
    });
    return { ok: resp.ok, status: resp.status };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

async function sendIlinkTextMessage(toUserId, text) {
  if (!state.bot_token || !toUserId || !text) {
    return { ok: false, error: '未登录微信 ClawBot 或缺少收件人' };
  }
  const baseUrl = (state.ilink_base_url || DEFAULT_ILINK_BASE).replace(/\/+$/, '');
  const ctxToken = state.context_tokens.get(toUserId) || '';
  const body = {
    msg: {
      from_user_id: '',
      to_user_id: toUserId,
      client_id: `ah_${Date.now()}_${crypto.randomBytes(3).toString('hex')}`,
      message_type: 2,
      message_state: 2,
      context_token: ctxToken,
      item_list: [{ type: 1, text_item: { text } }],
    },
    base_info: { channel_version: '1.0.0' },
  };
  const resp = await fetch(`${baseUrl}/ilink/bot/sendmessage`, {
    method: 'POST',
    headers: buildIlinkHeaders(),
    body: JSON.stringify(body),
  });
  const raw = await resp.text();
  return { ok: resp.ok, status: resp.status, body: raw.slice(0, 200) };
}

async function startGetUpdatesLoop() {
  if (state.polling_updates || !state.bot_token) return;
  state.polling_updates = true;
  console.log('[WeixinBridge] 已开启微信 iLink 长轮询收消息循环...');

  while (state.logged_in && state.bot_token) {
    try {
      const baseUrl = (state.ilink_base_url || DEFAULT_ILINK_BASE).replace(/\/+$/, '');
      const resp = await fetch(`${baseUrl}/ilink/bot/getupdates`, {
        method: 'POST',
        headers: buildIlinkHeaders(),
        body: JSON.stringify({
          get_updates_buf: state.get_updates_buf || '',
          base_info: { channel_version: '1.0.0' },
        }),
      });
      if (!resp.ok) {
        await new Promise((r) => setTimeout(r, 3000));
        continue;
      }
      const data = await resp.json();
      let needSave = false;
      if (typeof data.get_updates_buf === 'string' && data.get_updates_buf && data.get_updates_buf !== state.get_updates_buf) {
        state.get_updates_buf = data.get_updates_buf;
        needSave = true;
      }
      const msgs = Array.isArray(data.msgs) ? data.msgs : [];
      for (const m of msgs) {
        const fromUser = String(m.from_user_id || '').trim();
        const extracted = await extractAndHydrateIlinkMsg(m);
        if (!fromUser || (!extracted.text && extracted.attachments.length === 0)) continue;
        if (m.context_token && state.context_tokens.get(fromUser) !== String(m.context_token)) {
          state.context_tokens.set(fromUser, String(m.context_token));
          needSave = true;
        }
        // 收到消息瞬间立即点亮手机微信顶部的“对方正在输入中...”
        sendIlinkTyping(fromUser, 1).catch(() => {});
        const ingress = state.active_session?.ingress_url || state.ingress_url;
        if (ingress) {
          state.rx_count += 1;
          state.last_rx_text = (extracted.text || `[${extracted.msg_type}]`).slice(0, 60);
          const fwdHeaders = { 'Content-Type': 'application/json' };
          if (state.access_token) fwdHeaders['X-Access-Token'] = state.access_token;
          fetch(ingress, {
            method: 'POST',
            headers: fwdHeaders,
            body: JSON.stringify({
              text: extracted.text,
              msg_type: extracted.msg_type,
              attachments: extracted.attachments,
              quote_text: extracted.quote_text,
              has_media: extracted.has_media,
              from_wxid: fromUser,
              sender_id: fromUser,
              sender_name: fromUser,
              chat_id: fromUser,
              external_msg_id: String(m.message_id || m.seq || `wx_${Date.now()}`),
            }),
          })
            .then((r) => {
              state.last_forward_status = r.status;
              if (!r.ok) {
                console.error(`[WeixinBridge] 转发至 AgentHub 返回 HTTP ${r.status}`);
              }
            })
            .catch((e) => console.error('[WeixinBridge] 转发至 AgentHub 失败:', e));
        }
      }
      if (needSave) {
        savePersistedSession();
      }
    } catch (err) {
      await new Promise((r) => setTimeout(r, 2500));
    }
  }
  state.polling_updates = false;
}

async function pollQrStatusLoop(qrcodeId) {
  state.polling_qr = true;
  const startAt = Date.now();
  const maxWaitMs = 5 * 60 * 1000;

  while (state.qr_code_id === qrcodeId && Date.now() - startAt < maxWaitMs) {
    try {
      const resp = await fetch(
        `${DEFAULT_ILINK_BASE}/ilink/bot/get_qrcode_status?qrcode=${encodeURIComponent(qrcodeId)}`,
        { headers: { 'iLink-App-ClientVersion': '1' } }
      );
      if (resp.ok) {
        const data = await resp.json();
        const st = String(data.status || '').toLowerCase();
        if (st === 'wait' || st === 'scaned' || st === 'scanned') {
          state.qr_status = st === 'wait' ? 'wait' : 'scaned';
        } else if (st === 'expired') {
          state.qr_status = 'expired';
          break;
        } else if (st === 'confirmed' && data.bot_token) {
          state.qr_status = 'confirmed';
          state.logged_in = true;
          state.bot_token = String(data.bot_token);
          state.ilink_base_url = String(data.baseurl || DEFAULT_ILINK_BASE);
          state.wxid = String(data.ilink_user_id || data.ilink_bot_id || `wx_${Date.now()}`);
          state.account_label = state.wxid;
          savePersistedSession();
          console.log(`[WeixinBridge] 微信 ClawBot 扫码登录成功！user=${state.wxid}`);

          // 自动回调 AgentHub 完成用户绑定
          if (state.active_session?.claim_url) {
            try {
              const cHeaders = { 'Content-Type': 'application/json' };
              if (state.access_token) cHeaders['X-Access-Token'] = state.access_token;
              await fetch(state.active_session.claim_url, {
                method: 'POST',
                headers: cHeaders,
                body: JSON.stringify({
                  external_user_id: state.wxid,
                  external_user_name: `微信用户 (${state.wxid.slice(0, 12)})`,
                }),
              });
            } catch (e) {
              console.error('[WeixinBridge] 自动回调 AgentHub 绑定失败:', e);
            }
          }
          startGetUpdatesLoop();
          break;
        }
      }
    } catch {
      // 忽略单次轮询网络抖动
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  state.polling_qr = false;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://127.0.0.1:${PORT}`);

  if (req.method === 'GET' && url.pathname === '/status') {
    return sendJson(res, 200, {
      ok: true,
      bridge_online: true,
      logged_in: state.logged_in,
      qr_status: state.qr_status,
      account_label: state.account_label,
      wxid: state.wxid,
      qr_url: state.qr_url,
      ingress_configured: Boolean(state.ingress_url),
      rx_count: state.rx_count,
      last_rx_text: state.last_rx_text,
      last_forward_status: state.last_forward_status,
    });
  }

  if (req.method === 'POST' && url.pathname === '/qr/start') {
    const body = await readJsonBody(req);
    if (body.access_token) {
      state.access_token = String(body.access_token).trim();
    }
    state.active_session = {
      link_id: body.link_id || '',
      ticket: body.ticket || '',
      endpoint_token: body.endpoint_token || '',
      claim_url: String(body.claim_url || '').trim(),
      ingress_url: String(body.ingress_url || state.ingress_url || '').trim(),
      created_at: Date.now(),
    };
    if (body.ingress_url) {
      state.ingress_url = String(body.ingress_url);
    }

    try {
      const qrResp = await fetch(`${DEFAULT_ILINK_BASE}/ilink/bot/get_bot_qrcode?bot_type=3`);
      const qrData = await qrResp.json();
      if (!qrData?.qrcode || !qrData?.qrcode_img_content) {
        return sendJson(res, 502, { ok: false, error: '腾讯微信 iLink 未返回有效二维码', raw: qrData });
      }
      state.qr_code_id = String(qrData.qrcode);
      state.qr_url = String(qrData.qrcode_img_content);
      state.qr_status = 'wait';
      // 若当前已有有效 bot_token，保留在线收消息长轮询，直到新二维码真正扫码确认后再覆盖
      if (!state.bot_token) {
        state.logged_in = false;
      }

      // 异步后台持续轮询手机扫码结果
      pollQrStatusLoop(state.qr_code_id);

      return sendJson(res, 200, {
        ok: true,
        bridge_online: true,
        qrcode_id: state.qr_code_id,
        qr_url: state.qr_url,
        qr_status: state.qr_status,
        logged_in: state.logged_in,
      });
    } catch (err) {
      return sendJson(res, 502, { ok: false, error: `请求微信官方二维码失败: ${err}` });
    }
  }

  if (req.method === 'POST' && url.pathname === '/typing') {
    if (BRIDGE_SECRET && req.headers['x-bridge-secret'] !== BRIDGE_SECRET) {
      return sendJson(res, 401, { ok: false, error: 'Unauthorized bridge secret' });
    }
    const body = await readJsonBody(req);
    const toWxid = String(body.to_wxid || state.wxid || '').trim();
    const status = Number(body.status) === 2 ? 2 : 1;
    const r = await sendIlinkTyping(toWxid, status);
    return sendJson(res, 200, r);
  }

  if (req.method === 'POST' && url.pathname === '/send') {
    if (BRIDGE_SECRET && req.headers['x-bridge-secret'] !== BRIDGE_SECRET) {
      return sendJson(res, 401, { ok: false, error: 'Unauthorized bridge secret' });
    }
    const body = await readJsonBody(req);
    const toWxid = String(body.to_wxid || body.chat_id || state.wxid || '').trim();
    const chunks = Array.isArray(body.chunks) ? body.chunks : [];
    const results = [];
    for (const chunk of chunks) {
      if (!chunk) continue;
      results.push(await sendIlinkTextMessage(toWxid, String(chunk)));
    }
    return sendJson(res, 200, { ok: true, delivered_chunks: results.filter((r) => r.ok).length, results });
  }

  if (req.method === 'POST' && url.pathname === '/config') {
    if (BRIDGE_SECRET && req.headers['x-bridge-secret'] !== BRIDGE_SECRET) {
      return sendJson(res, 401, { ok: false, error: 'Unauthorized bridge secret' });
    }
    const body = await readJsonBody(req);
    if (body.ingress_url) {
      state.ingress_url = String(body.ingress_url).trim();
    }
    if (body.access_token) {
      state.access_token = String(body.access_token).trim();
    }
    savePersistedSession();
    if (state.logged_in && state.bot_token && !state.polling_updates) {
      startGetUpdatesLoop();
    }
    return sendJson(res, 200, {
      ok: true,
      logged_in: state.logged_in,
      polling_updates: state.polling_updates,
      ingress_configured: Boolean(state.ingress_url),
    });
  }

  return sendJson(res, 404, { ok: false, error: 'Not Found' });
});

server.listen(PORT, '127.0.0.1', () => {
  loadPersistedSession();
  console.log(`[AgentHub Weixin ClawBot Bridge] listening on http://127.0.0.1:${PORT} (Tencent iLink Ready)`);
  if (state.logged_in && state.bot_token) {
    console.log(`[WeixinBridge] 已自动恢复历史微信登录态: ${state.wxid}`);
    startGetUpdatesLoop();
  }
});
