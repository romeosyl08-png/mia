const STATIC_CACHE = "2ai-public-static-v5";
const STATIC_PREFIX = "/static/";

self.addEventListener("install", event => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(
      names
        .filter(name => name.startsWith("2ai-public-static-") && name !== STATIC_CACHE)
        .map(name => caches.delete(name))
    );
    await self.clients.claim();
  })());
});

function unavailableResponse() {
  return new Response(
    "<!doctype html><html lang=\"fr\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>2AI hors ligne</title><body><main><h1>Connexion Internet requise</h1><p>2AI a besoin d'une connexion pour ouvrir tes espaces et conversations. Les pages privées ne sont pas enregistrées hors ligne.</p><a href=\"/\">Réessayer</a></main></body></html>",
    {
      status: 503,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff"
      }
    }
  );
}

async function fetchStaticAsset(request, cachedResponse) {
  let response;
  try {
    response = await fetch(request);
  } catch (error) {
    if (error instanceof TypeError) return cachedResponse || null;
    throw error;
  }
  const cacheControl = response.headers.get("Cache-Control") || "";
  if (response.ok && response.type === "basic" && !/\bno-store\b/i.test(cacheControl)) {
    try {
      const cache = await caches.open(STATIC_CACHE);
      await cache.put(request, response.clone());
    } catch (error) {
      console.error("2AI could not cache a public static asset.", error);
    }
  }
  return response;
}

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;

  if (url.pathname.startsWith(STATIC_PREFIX)) {
    event.respondWith((async () => {
      const cache = await caches.open(STATIC_CACHE);
      const cachedResponse = await cache.match(request);
      const update = fetchStaticAsset(request, cachedResponse);
      // The page, scripts and Wasm modules must use the same deployed version.
      if (/\.(?:js|wasm)$/.test(url.pathname)) {
        return (await update) || unavailableResponse();
      }
      if (cachedResponse) {
        event.waitUntil(update);
        return cachedResponse;
      }
      return (await update) || unavailableResponse();
    })());
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => unavailableResponse()));
  }
});
