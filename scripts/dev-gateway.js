const fs = require('fs');
const http = require('http');
const https = require('https');
const net = require('net');
const path = require('path');
const { URL } = require('url');

const port = Number(process.env.PORT || 3000);
const backendOrigin = new URL(process.env.BACKEND_ORIGIN || 'http://127.0.0.1:8000');
const frontendDir = path.resolve(__dirname, '..', 'frontend');
const publicAppUrl = (process.env.PUBLIC_APP_URL || '').trim();

const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.txt': 'text/plain; charset=utf-8',
  '.webp': 'image/webp',
};

function firstForwardedValue(value, fallback = '') {
  if (Array.isArray(value)) {
    value = value[0];
  }
  if (typeof value !== 'string') {
    return fallback;
  }
  const normalized = value.split(',')[0].trim();
  return normalized || fallback;
}

function getBaseUrl(req) {
  if (publicAppUrl) {
    return publicAppUrl.replace(/\/+$/, '');
  }
  const rawProto = firstForwardedValue(req.headers['x-forwarded-proto'], 'http').replace(/:$/, '');
  const proto = rawProto === 'https' ? 'https' : 'http';
  const host = firstForwardedValue(
    req.headers['x-forwarded-host'],
    firstForwardedValue(req.headers.host, `127.0.0.1:${port}`),
  );
  return `${proto}://${host}`;
}

function sendJson(res, code, payload) {
  res.writeHead(code, {
    'Cache-Control': 'no-store',
    'Content-Type': 'application/json; charset=utf-8',
  });
  res.end(JSON.stringify(payload, null, 2));
}

function serveFile(res, filePath) {
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('Not found');
      return;
    }
    const ext = path.extname(filePath).toLowerCase();
    const noStoreExtensions = new Set(['.html', '.js', '.json']);
    res.writeHead(200, {
      'Cache-Control': noStoreExtensions.has(ext) ? 'no-store' : 'public, max-age=300',
      'Content-Type': contentTypes[ext] || 'application/octet-stream',
    });
    res.end(data);
  });
}

function resolveFrontendPath(urlPath) {
  const cleanPath = decodeURIComponent(urlPath.split('?')[0]);
  const requested = cleanPath === '/' ? '/index.html' : cleanPath;
  const normalized = path.normalize(requested).replace(/^(\.\.[/\\])+/, '');
  return path.join(frontendDir, normalized);
}

function maybeServeStatic(req, res) {
  const filePath = resolveFrontendPath(req.url);
  if (!filePath.startsWith(frontendDir)) {
    res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Forbidden');
    return true;
  }
  if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
    serveFile(res, filePath);
    return true;
  }
  if (!path.extname(filePath)) {
    serveFile(res, path.join(frontendDir, 'index.html'));
    return true;
  }
  return false;
}

function proxyHttp(req, res) {
  const upstream = backendOrigin.protocol === 'https:' ? https : http;
  const proxyReq = upstream.request({
    protocol: backendOrigin.protocol,
    hostname: backendOrigin.hostname,
    port: backendOrigin.port,
    method: req.method,
    path: req.url,
    headers: {
      ...req.headers,
      host: backendOrigin.host,
    },
  }, (proxyRes) => {
    res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
    proxyRes.pipe(res);
  });

  proxyReq.on('error', (err) => {
    sendJson(res, 502, { detail: `Gateway proxy error: ${err.message}` });
  });

  req.pipe(proxyReq);
}

function proxyWebSocket(req, socket, head) {
  const target = net.connect(Number(backendOrigin.port || 80), backendOrigin.hostname, () => {
    const headerLines = [];
    for (let i = 0; i < req.rawHeaders.length; i += 2) {
      const key = req.rawHeaders[i];
      const value = req.rawHeaders[i + 1];
      if (key.toLowerCase() === 'host') {
        headerLines.push(`Host: ${backendOrigin.host}`);
      } else {
        headerLines.push(`${key}: ${value}`);
      }
    }

    const requestHead = `GET ${req.url} HTTP/${req.httpVersion}\r\n${headerLines.join('\r\n')}\r\n\r\n`;
    target.write(requestHead);
    if (head && head.length) target.write(head);
    socket.pipe(target).pipe(socket);
  });

  target.on('error', () => {
    socket.destroy();
  });

  socket.on('error', () => target.destroy());
}

const server = http.createServer((req, res) => {
  if (!req.url) {
    res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Bad request');
    return;
  }

  if (req.url.startsWith('/tonconnect-manifest.json')) {
    const baseUrl = getBaseUrl(req);
    sendJson(res, 200, {
      url: baseUrl,
      name: 'TON Prediction',
      iconUrl: `${baseUrl}/icon-180.png`,
      termsOfUseUrl: `${baseUrl}/terms.html`,
      privacyPolicyUrl: `${baseUrl}/privacy.html`,
    });
    return;
  }

  if (
    req.url.startsWith('/api/v1/')
    || req.url === '/health'
    || req.url === '/docs'
    || req.url === '/openapi.json'
  ) {
    proxyHttp(req, res);
    return;
  }

  if (!maybeServeStatic(req, res)) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Not found');
  }
});

server.on('upgrade', (req, socket, head) => {
  if (req.url && req.url.startsWith('/api/v1/ws/')) {
    proxyWebSocket(req, socket, head);
    return;
  }
  socket.destroy();
});

server.listen(port, () => {
  console.log(`TON Pred dev gateway listening on http://127.0.0.1:${port}`);
  console.log(`Proxying API to ${backendOrigin.href}`);
});
