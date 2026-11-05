import http from 'http'
http.createServer((q, r) => { r.setHeader('content-type', 'application/json'); r.end(q.url.startsWith('/api/health') ? '{"auth_enabled":false}' : '{}') }).listen(8000, '127.0.0.1')
