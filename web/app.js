async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleDateString();
}

function getQueryParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

function renderEmpty(container, message) {
  container.innerHTML = `<div class="empty-state">${message}</div>`;
}

function renderHistoryChart(container, history) {
  if (!history.length) {
    renderEmpty(container, "No ranking history available for this game yet.");
    return;
  }

  const width = 760;
  const height = 240;
  const padding = 28;
  const maxRank = Math.max(...history.map((entry) => entry.rank));
  const minRank = Math.min(...history.map((entry) => entry.rank));
  const xStep = history.length > 1 ? (width - padding * 2) / (history.length - 1) : 0;
  const yRange = Math.max(maxRank - minRank, 1);

  const points = history.map((entry, index) => {
    const x = padding + index * xStep;
    const normalized = (entry.rank - minRank) / yRange;
    const y = padding + normalized * (height - padding * 2);
    return { x, y, ...entry };
  });

  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`)
    .join(" ");

  container.innerHTML = `
    <svg class="chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="Rank history chart">
      <text class="chart-axis" x="${padding}" y="16">Rank ${minRank}</text>
      <text class="chart-axis" x="${padding}" y="${height - 8}">Rank ${maxRank}</text>
      <path class="chart-line" d="${path}"></path>
      ${points
        .map(
          (point) => `
            <circle class="chart-point" cx="${point.x}" cy="${point.y}" r="4"></circle>
          `
        )
        .join("")}
      ${points
        .filter((_, index) => index === 0 || index === points.length - 1 || index % Math.ceil(points.length / 4) === 0)
        .map(
          (point) => `
            <text class="chart-label" x="${point.x}" y="${height - 10}" text-anchor="middle">${point.capture_date.slice(5)}</text>
          `
        )
        .join("")}
    </svg>
  `;
}

async function initHome() {
  const storefrontSelect = document.querySelector("#storefront-select");
  const selectedStorefrontLabel = document.querySelector("#selected-storefront-label");
  const captureDateLabel = document.querySelector("#capture-date-label");
  const rankingSubtitle = document.querySelector("#ranking-subtitle");
  const rankingList = document.querySelector("#ranking-list");
  const searchInput = document.querySelector("#search-input");
  const searchButton = document.querySelector("#search-button");
  const searchResults = document.querySelector("#search-results");

  const storefrontData = await fetchJson("/storefronts");
  const storefronts = storefrontData.storefronts;

  storefrontSelect.innerHTML = storefronts
    .map(
      (storefront) =>
        `<option value="${storefront.slug}">${storefront.name} · ${storefront.platform_slug}</option>`
    )
    .join("");

  async function loadRankings(storefrontSlug) {
    const data = await fetchJson(`/rankings/current?storefront=${encodeURIComponent(storefrontSlug)}`);
    selectedStorefrontLabel.textContent = data.storefront.name;
    captureDateLabel.textContent = formatDate(data.capture_date);
    rankingSubtitle.textContent = `${data.entries.length} visible ranks`;
    rankingList.innerHTML = data.entries
      .map(
        (entry) => `
          <article class="ranking-item">
            <div class="ranking-rank">${entry.rank}</div>
            <div class="ranking-meta">
              <strong>${entry.canonical_name}</strong>
              <span>${entry.alias_title}</span>
            </div>
            <a class="ranking-link" href="/game?id=${entry.game_id}">Open history</a>
          </article>
        `
      )
      .join("");
  }

  async function runSearch() {
    const query = searchInput.value.trim();
    if (!query) {
      renderEmpty(searchResults, "Write a game title to search.");
      return;
    }

    const data = await fetchJson(`/games/search?q=${encodeURIComponent(query)}`);
    if (!data.results.length) {
      renderEmpty(searchResults, "No matching games found.");
      return;
    }

    searchResults.innerHTML = data.results
      .map(
        (result) => `
          <article class="search-result">
            <div>
              <strong>${result.canonical_name}</strong>
              <span>${result.example_alias || "No alias"} · ${result.platform_count} platform(s)</span>
            </div>
            <a href="/game?id=${result.game_id}">View detail</a>
          </article>
        `
      )
      .join("");
  }

  storefrontSelect.addEventListener("change", () => loadRankings(storefrontSelect.value));
  searchButton.addEventListener("click", runSearch);
  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") runSearch();
  });

  if (storefronts.length) {
    await loadRankings(storefronts[0].slug);
  } else {
    renderEmpty(rankingList, "No storefronts configured yet.");
  }
}

async function initGame() {
  const gameId = getQueryParam("id");
  const gameTitle = document.querySelector("#game-title");
  const gameSummary = document.querySelector("#game-summary");
  const bestRankLabel = document.querySelector("#best-rank-label");
  const lastSeenLabel = document.querySelector("#last-seen-label");
  const aliasesList = document.querySelector("#aliases-list");
  const historyChart = document.querySelector("#history-chart");
  const historyTable = document.querySelector("#history-table");

  if (!gameId) {
    gameTitle.textContent = "Missing game id";
    gameSummary.textContent = "Open this page from search results or a ranking row.";
    return;
  }

  const [summary, history] = await Promise.all([
    fetchJson(`/games/${gameId}`),
    fetchJson(`/games/${gameId}/history`),
  ]);

  gameTitle.textContent = summary.canonical_name;
  gameSummary.textContent = `${summary.ranking_points} ranking points collected across all tracked storefronts.`;
  bestRankLabel.textContent = summary.best_rank ?? "-";
  lastSeenLabel.textContent = formatDate(summary.last_seen_date);

  aliasesList.innerHTML = summary.aliases.length
    ? summary.aliases
        .map(
          (alias) => `
            <article class="alias-item">
              <strong>${alias.alias_title}</strong>
              <span>${alias.storefront_name}</span>
            </article>
          `
        )
        .join("")
    : `<div class="empty-state">No aliases recorded.</div>`;

  renderHistoryChart(historyChart, history.history);

  if (!history.history.length) {
    renderEmpty(historyTable, "No historical rows available.");
    return;
  }

  historyTable.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th>Storefront</th>
          <th>Rank</th>
          <th>Alias</th>
        </tr>
      </thead>
      <tbody>
        ${history.history
          .map(
            (entry) => `
              <tr>
                <td>${formatDate(entry.capture_date)}</td>
                <td>${entry.storefront}</td>
                <td>${entry.rank}</td>
                <td>${entry.alias_title}</td>
              </tr>
            `
          )
          .join("")}
      </tbody>
    </table>
  `;
}

async function main() {
  try {
    const page = document.body.dataset.page;
    if (page === "home") {
      await initHome();
    } else if (page === "game") {
      await initGame();
    }
  } catch (error) {
    const target = document.querySelector(".layout") || document.body;
    target.insertAdjacentHTML(
      "afterbegin",
      `<div class="panel"><div class="empty-state">UI error: ${error.message}</div></div>`
    );
  }
}

main();
