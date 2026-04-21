const deck = document.getElementById("deck");
const peekCard = document.getElementById("peekCard");
const skipButton = document.getElementById("skipButton");
const boostButton = document.getElementById("boostButton");
const sourceButton = document.getElementById("sourceButton");

const LOW_WATER_MARK = 8;

let allStories = [];
let feed = [];
let seenLinks = new Set();
let currentStory = null;
let isFetching = false;
const pageParams = new URLSearchParams(window.location.search);
const localeConfig = {
  hl: pageParams.get("hl") || "",
  gl: pageParams.get("gl") || "",
  ceid: pageParams.get("ceid") || ""
};

function shuffle(array) {
  const clone = [...array];
  for (let i = clone.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [clone[i], clone[j]] = [clone[j], clone[i]];
  }
  return clone;
}

function dedupeStories(items) {
  const unique = [];
  const seen = new Set();
  items.forEach((item) => {
    if (!item?.sourceUrl || seen.has(item.sourceUrl)) {
      return;
    }
    seen.add(item.sourceUrl);
    unique.push(item);
  });
  return unique;
}

function updateSource(story) {
  sourceButton.href = story?.sourceUrl || "#";
}

function fallbackImage(story) {
  const source = (story?.sourceName || "Google News").slice(0, 28);
  const topic = story?.topic || "Top stories";
  const region = story?.region || "Global";
  const svg =
    `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1200 675'>` +
    `<defs><linearGradient id='g' x1='0' x2='1' y1='0' y2='1'>` +
    `<stop stop-color='#f5ecdf'/><stop offset='0.55' stop-color='#ead8bf'/>` +
    `<stop offset='1' stop-color='#ddb586'/></linearGradient></defs>` +
    `<rect width='1200' height='675' fill='url(#g)'/>` +
    `<circle cx='1030' cy='110' r='220' fill='rgba(203,91,45,0.18)'/>` +
    `<circle cx='150' cy='560' r='120' fill='rgba(255,255,255,0.22)'/>` +
    `<rect x='78' y='86' width='196' height='196' rx='42' fill='rgba(255,255,255,0.58)'/>` +
    `<text x='92' y='404' font-family='Arial, sans-serif' font-size='66' font-weight='700' fill='#241914'>${source}</text>` +
    `<text x='92' y='478' font-family='Arial, sans-serif' font-size='34' fill='#5c4638'>${topic} • ${region}</text>` +
    `</svg>`;
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function proxyImage(url) {
  const clean = (url || "").trim();
  if (!clean) {
    return "";
  }

  if (clean.includes("wsrv.nl/?url=")) {
    return clean;
  }

  if (clean.includes("news.google.com/api/attachments/")) {
    return `https://wsrv.nl/?url=${encodeURIComponent(clean)}&w=1200&h=675&fit=cover&output=jpg`;
  }

  return clean;
}

function imageFor(story) {
  return proxyImage(story?.image) || fallbackImage(story);
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }

  return new Intl.DateTimeFormat("tr-TR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Istanbul"
  }).format(date);
}

function pushFreshStories(items) {
  const uniqueIncoming = dedupeStories(items);
  const unseen = uniqueIncoming.filter((item) => !seenLinks.has(item.sourceUrl));

  unseen.forEach((item) => seenLinks.add(item.sourceUrl));
  allStories = dedupeStories([...uniqueIncoming, ...allStories]);
  feed = [...feed, ...shuffle(unseen)];

  if (feed.length < LOW_WATER_MARK && allStories.length) {
    feed = [...feed, ...shuffle(allStories)];
  }
}

async function fetchStories(force = false) {
  if (isFetching) {
    return;
  }

  isFetching = true;

  try {
    const cacheMode = force ? "no-store" : "default";
    const response = await fetch(`./news-data.json?t=${Date.now()}`, { cache: cacheMode || "no-store" });
    if (!response.ok) {
      throw new Error(`JSON request failed: ${response.status}`);
    }

    const payload = await response.json();
    const items = payload.items || [];
    const filtered = localeConfig.ceid
      ? items.filter((item) => item.ceid === localeConfig.ceid)
      : items;
    pushFreshStories(filtered.length ? filtered : items);
    renderDeck();
  } catch (error) {
    if (!deck.children.length) {
      deck.innerHTML = `<div class="error-state">JSON veri yuklenemedi.<br />news-data.json dosyasini kontrol et.</div>`;
    }
    console.error(error);
  } finally {
    isFetching = false;
  }
}

function ensureFeedDepth() {
  if (feed.length >= LOW_WATER_MARK || !allStories.length) {
    return;
  }

  feed = [...feed, ...shuffle(allStories)];
}

function updatePeekCard() {
  const nextStory = feed[1];
  if (!nextStory) {
    peekCard.innerHTML = "";
    return;
  }

  peekCard.innerHTML = `
    <img class="peek-image" src="${imageFor(nextStory)}" alt="" />
    <div class="source-badge source-badge-peek">
      <img class="source-favicon" src="${nextStory.favicon || ""}" alt="" />
      <span>${nextStory.sourceName}</span>
    </div>
    <p class="peek-meta">${nextStory.topic}</p>
    <h3 class="peek-title">${nextStory.title}</h3>
    <p class="peek-summary">${nextStory.summary}</p>
    <div></div>
    <p class="peek-source">${nextStory.sourceName} - ${formatDate(nextStory.pubDate)}</p>
  `;
}

function createCard(story, index) {
  const card = document.createElement("a");
  card.className = "news-card";
  card.dataset.index = String(index);
  card.href = story.sourceUrl;
  card.target = "_blank";
  card.rel = "noreferrer";
  card.innerHTML = `
    <img class="card-image" src="${imageFor(story)}" alt="${story.title}" referrerpolicy="no-referrer" />
    <div class="source-badge">
      <img class="source-favicon" src="${story.favicon || ""}" alt="" />
      <span>${story.sourceName}</span>
    </div>
    <div class="card-meta">
      <span>${story.topic}</span>
    </div>
    <h2 class="card-title">${story.title}</h2>
    <p class="card-summary">${story.summary}</p>
    <div></div>
    <p class="card-source">${story.sourceName} - ${formatDate(story.pubDate)}</p>
  `;

  let startX = 0;
  let currentX = 0;
  let isPointerDown = false;

  const onPointerMove = (event) => {
    if (!isPointerDown) {
      return;
    }

    currentX = event.clientX - startX;
    const rotation = currentX / 26;
    peekCard.classList.add("is-visible");
    card.classList.add("dragging");
    card.style.transform = `translateX(${currentX}px) rotate(${rotation}deg)`;
  };

  const endDrag = (event) => {
    if (!isPointerDown) {
      return;
    }

    isPointerDown = false;
    if (typeof event?.pointerId === "number" && card.hasPointerCapture?.(event.pointerId)) {
      card.releasePointerCapture(event.pointerId);
    }

    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", endDrag);
    window.removeEventListener("pointercancel", endDrag);
    peekCard.classList.remove("is-visible");

    if (currentX < -100) {
      removeTopCard("left");
      return;
    }

    if (currentX > 100) {
      removeTopCard("right");
      return;
    }

    card.classList.remove("dragging");
    card.style.transform = "";
  };

  card.addEventListener("pointerdown", (event) => {
    if (index !== 0) {
      return;
    }

    currentStory = story;
    updateSource(story);
    isPointerDown = true;
    startX = event.clientX;
    currentX = 0;
    card.setPointerCapture(event.pointerId);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
  });

  card.addEventListener("click", (event) => {
    currentStory = story;
    updateSource(story);
    if (Math.abs(currentX) > 8) {
      event.preventDefault();
    }
  });

  return card;
}

function renderDeck() {
  ensureFeedDepth();

  if (!feed.length) {
    deck.innerHTML = `<div class="loading-state">Canli haber akisi yukleniyor...</div>`;
    return;
  }

  const visibleStories = feed.slice(0, 3);
  deck.innerHTML = "";
  visibleStories.forEach((story, index) => {
    deck.appendChild(createCard(story, index));
  });
  currentStory = visibleStories[0];
  updateSource(currentStory);
  updatePeekCard();
}

function prioritizeTopic(story) {
  if (!story) {
    return;
  }

  const matching = shuffle(allStories.filter((item) => item.topic === story.topic));
  const other = feed.filter((item) => item.topic !== story.topic);
  feed = [...matching, ...other];
}

function removeTopCard(direction) {
  const firstCard = deck.querySelector(".news-card");
  if (!firstCard) {
    return;
  }

  peekCard.classList.add("is-visible");
  peekCard.classList.add("is-transitioning");
  deck.classList.add("is-transitioning");
  firstCard.classList.remove("dragging");
  firstCard.classList.add(direction === "left" ? "leaving-left" : "leaving-right");

  if (direction === "right" && currentStory) {
    prioritizeTopic(currentStory);
  }

  window.setTimeout(() => {
    feed.shift();
    peekCard.classList.remove("is-visible");
    peekCard.classList.remove("is-transitioning");
    deck.classList.remove("is-transitioning");
    renderDeck();
    ensureFeedDepth();
    if (feed.length < LOW_WATER_MARK) {
      fetchStories(false);
    }
  }, 240);
}

skipButton.addEventListener("click", () => {
  removeTopCard("left");
});

boostButton.addEventListener("click", () => {
  removeTopCard("right");
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    fetchStories(false);
  }
});

renderDeck();
fetchStories(false);
