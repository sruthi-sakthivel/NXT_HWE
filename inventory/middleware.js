import { next } from '@vercel/functions';

// Gate every request except the login page and the auth endpoint that
// establishes the session cookie in the first place.
export const config = {
  matcher: ['/((?!login.html|api/auth).*)'],
};

const COOKIE_NAME = 'nx_session';
const encoder = new TextEncoder();

function base64urlDecode(str) {
  const padded = str.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (str.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function getCookie(request, name) {
  const header = request.headers.get('cookie');
  if (!header) return null;
  const match = header.match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`));
  return match ? decodeURIComponent(match[1]) : null;
}

// Verifies the HMAC-SHA256 signature minted by api/auth.js and checks
// expiry. Not shared as a module with api/auth.js on purpose — keeping
// each entrypoint self-contained avoids cross-runtime bundling surprises
// between the Edge (this file) and Node.js (api/auth.js) runtimes.
async function verifySession(token, secret) {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 2) return null;
  const [payloadB64, sigB64] = parts;
  const key = await crypto.subtle.importKey(
    'raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']
  );
  const valid = await crypto.subtle.verify('HMAC', key, base64urlDecode(sigB64), encoder.encode(payloadB64));
  if (!valid) return null;
  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(base64urlDecode(payloadB64)));
  } catch {
    return null;
  }
  if (!payload.exp || payload.exp < Math.floor(Date.now() / 1000)) return null;
  return payload;
}

export default async function middleware(request) {
  const secret = process.env.SESSION_SECRET;
  const token = getCookie(request, COOKIE_NAME);
  const session = secret ? await verifySession(token, secret) : null;

  if (session && session.email) {
    return next();
  }

  const url = new URL(request.url);
  const loginUrl = new URL('/login.html', url.origin);
  loginUrl.searchParams.set('next', url.pathname + url.search);
  return Response.redirect(loginUrl, 307);
}
