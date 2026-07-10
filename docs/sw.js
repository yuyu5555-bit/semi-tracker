// 半導体テーマトラッカー Service Worker
// シェル(index.html)も data.json も ネット優先。取得失敗時(オフライン等)のみ前回キャッシュを使う。
// ※旧バージョンは index.html をキャッシュ優先にしていたため、更新しても反映されにくい問題があった。
const SHELL = "semi-tt-shell-v5";
const ASSETS = ["./", "index.html", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  const isShellDoc = e.request.mode === "navigate" || url.pathname.endsWith("index.html") || url.pathname.endsWith("/");
  if (url.pathname.endsWith("data.json") || isShellDoc) {
    // ネット優先 → 失敗時キャッシュ(オフラインでも前回内容を表示)
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const copy = res.clone();
          const key = url.pathname.endsWith("data.json") ? "data.json" : e.request;
          caches.open(SHELL).then((c) => c.put(key, copy));
          return res;
        })
        .catch(() => caches.match(url.pathname.endsWith("data.json") ? "data.json" : e.request))
    );
    return;
  }
  // その他の静的アセット(アイコン等)は変化が少ないのでキャッシュ優先のまま
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
});
