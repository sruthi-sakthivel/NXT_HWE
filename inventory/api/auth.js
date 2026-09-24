const CLIENT_ID = '863545530166-2ap7tl7cpntv0dhs95lqug5rn7tavgcj.apps.googleusercontent.com';
const ALLOWED_DOMAIN = 'nextsense.io';
const COOKIE_NAME = 'nx_session';
const SESSION_TTL_SECONDS = 60 * 60 * 24 * 30; // 30 days

const encoder = new TextEncoder();

function base64urlEncode(bytes) {
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// Mints our own signed session cookie. Not shared as a module with
// middleware.js on purpose — keeping each entrypoint self-contained avoids
// cross-runtime bundling surprises between Node.js (this file) and Edge
// (middleware.js).
async function signSession(payload, secret) {
  const payloadB64 = base64urlEncode(encoder.encode(JSON.stringify(payload)));
  const key = await crypto.subtle.importKey(
    'raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const sigBytes = new Uint8Array(await crypto.subtle.sign('HMAC', key, encoder.encode(payloadB64)));
  return `${payloadB64}.${base64urlEncode(sigBytes)}`;
}

export async function POST(request) {
  const secret = process.env.SESSION_SECRET;
  if (!secret) {
    return Response.json({ error: 'Server is not configured (missing SESSION_SECRET).' }, { status: 500 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: 'Malformed request.' }, { status: 400 });
  }

  const credential = body && body.credential;
  if (!credential) {
    return Response.json({ error: 'Missing credential.' }, { status: 400 });
  }

  // Google verifies the ID token's signature for us — no JWT library needed.
  const verifyRes = await fetch(
    `https://oauth2.googleapis.com/tokeninfo?id_token=${encodeURIComponent(credential)}`
  );
  if (!verifyRes.ok) {
    return Response.json({ error: 'Could not verify Google sign-in.' }, { status: 403 });
  }
  const claims = await verifyRes.json();

  if (claims.aud !== CLIENT_ID) {
    return Response.json({ error: 'Token was not issued for this app.' }, { status: 403 });
  }
  if (claims.email_verified !== 'true' && claims.email_verified !== true) {
    return Response.json({ error: 'Email not verified.' }, { status: 403 });
  }
  const email = typeof claims.email === 'string' ? claims.email.toLowerCase() : '';
  if (!email.endsWith(`@${ALLOWED_DOMAIN}`)) {
    return Response.json({ error: `Access restricted to @${ALLOWED_DOMAIN} accounts.` }, { status: 403 });
  }

  const exp = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
  const token = await signSession({ email, exp }, secret);

  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      'Set-Cookie': `${COOKIE_NAME}=${encodeURIComponent(token)}; Path=/; Max-Age=${SESSION_TTL_SECONDS}; HttpOnly; Secure; SameSite=Lax`,
    },
  });
}
