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

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function formatDataSource(value) {
  const mapping = {
    observed: "Observed",
    sheet_import: "Imported",
    imputed: "Imputed",
  };
  return mapping[value] || value || "Unknown";
}

function badgeClass(value) {
  return `source-badge source-badge--${value || "unknown"}`;
}

function filterHistory(history, { hideImputed = false, range = "all" } = {}) {
  let filtered = hideImputed ? history.filter((entry) => entry.data_source !== "imputed") : [...history];
  if (!filtered.length) return filtered;

  if (range !== "all") {
    const days = Number(range);
    const lastDate = new Date(filtered[filtered.length - 1].capture_date);
    const threshold = new Date(lastDate);
    threshold.setDate(lastDate.getDate() - (days - 1));
    filtered = filtered.filter((entry) => new Date(entry.capture_date) >= threshold);
  }

  return filtered;
}

function computeWindowAverage(history) {
  if (!history.length) return null;
  return history.reduce((sum, entry) => sum + entry.rank, 0) / history.length;
}

function metadataGroupForStorefront(storefrontSlug) {
  if (!storefrontSlug) return null;
  if (storefrontSlug.startsWith("nutaku-")) return "nutaku";
  if (storefrontSlug === "erolabs-home-ranking") return "erolabs";
  return storefrontSlug;
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
    <div class="chart-tooltip" id="chart-tooltip" hidden></div>
    <svg class="chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="Rank history chart">
      <text class="chart-axis" x="${padding}" y="16">Rank ${minRank}</text>
      <text class="chart-axis" x="${padding}" y="${height - 8}">Rank ${maxRank}</text>
      <path class="chart-line" d="${path}"></path>
      ${points
        .map(
          (point, index) => `
            <circle class="chart-point chart-point--${point.data_source}" cx="${point.x}" cy="${point.y}" r="4"></circle>
            <circle
              class="chart-hit"
              data-index="${index}"
              cx="${point.x}"
              cy="${point.y}"
              r="12"
              fill="transparent"
            ></circle>
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

  const tooltip = container.querySelector("#chart-tooltip");
  const svg = container.querySelector(".chart-svg");
  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

  function placeTooltip(point, event) {
    const rect = svg.getBoundingClientRect();
    const relativeX = event ? event.clientX - rect.left : (point.x / width) * rect.width;
    const relativeY = event ? event.clientY - rect.top : (point.y / height) * rect.height;
    const left = clamp(relativeX, 84, rect.width - 84);
    const top = clamp(relativeY - 18, 22, rect.height - 18);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }

  container.querySelectorAll(".chart-hit").forEach((node) => {
    node.addEventListener("mouseenter", (event) => {
      const point = points[Number(node.dataset.index)];
      tooltip.hidden = false;
      tooltip.innerHTML = `
        <span>${formatDate(point.capture_date)}</span>
        <strong>Rank #${point.rank}</strong>
        <span>${point.storefront_name || point.storefront}</span>
        <span>${formatDataSource(point.data_source)}</span>
      `;
      placeTooltip(point, event);
    });
    node.addEventListener("mousemove", (event) => {
      const point = points[Number(node.dataset.index)];
      placeTooltip(point, event);
    });
    node.addEventListener("mouseleave", () => {
      tooltip.hidden = true;
    });
  });
}

async function initHome() {
  const selectedStorefrontLabel = document.querySelector("#selected-storefront-label");
  const captureDateLabel = document.querySelector("#capture-date-label");
  const snapshotTypeLabel = document.querySelector("#snapshot-type-label");
  const rankingSubtitle = document.querySelector("#ranking-subtitle");
  const nutakuBoard = document.querySelector("#nutaku-board");
  const erolabsBoard = document.querySelector("#erolabs-board");
  const nutakuBoardSubtitle = document.querySelector("#nutaku-board-subtitle");
  const erolabsBoardSubtitle = document.querySelector("#erolabs-board-subtitle");
  const nutakuMoreLink = document.querySelector("#nutaku-more-link");
  const searchInput = document.querySelector("#search-input");
  const searchButton = document.querySelector("#search-button");
  const searchResults = document.querySelector("#search-results");
  const homeHideImputed = document.querySelector("#home-hide-imputed");
  const homeViewSelector = document.querySelector("#home-view-selector");
  let selectedView = "current";

  function renderMovement(entry) {
    if (entry.is_new) {
      return `<span class="movement movement--new">New</span>`;
    }
    if (!entry.movement) {
      return `<span class="movement movement--flat">0</span>`;
    }
    if (entry.movement > 0) {
      return `<span class="movement movement--up">${entry.movement}▲</span>`;
    }
    return `<span class="movement movement--down">${Math.abs(entry.movement)}▼</span>`;
  }

  async function runSearch() {
    const query = searchInput.value.trim();
    if (!query) {
      renderEmpty(searchResults, "Write a game title to search.");
      searchResults.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }

    renderEmpty(searchResults, `Searching for "${query}"...`);
    searchResults.scrollIntoView({ behavior: "smooth", block: "start" });

    const data = await fetchJson(`/games/search?q=${encodeURIComponent(query)}`);
    if (!data.results.length) {
      renderEmpty(searchResults, "No matching games found.");
      searchResults.scrollIntoView({ behavior: "smooth", block: "start" });
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
    searchResults.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderCompactBoard(container, entries, maxItems) {
    container.innerHTML = entries
      .slice(0, maxItems)
      .map(
        (entry) => `
          <article class="compact-row">
            <div class="compact-row__left">
              <span class="compact-row__rank">${entry.position}</span>
              <span class="compact-row__movement">${renderMovement(entry)}</span>
            </div>
            <div class="compact-row__body">
              <strong>${entry.canonical_name}</strong>
              <span>${entry.alias_title}</span>
            </div>
            <div class="compact-row__meta">
              <span class="compact-row__value">${formatNumber(entry.metric_value)}</span>
              <a class="ranking-link" href="/game?id=${entry.game_id}">Open</a>
            </div>
          </article>
        `
      )
      .join("");
  }

  async function loadCompactLeaderboards() {
    const [nutakuCurrent, erolabsCurrent, nutakuCompact, erolabsCompact] = await Promise.all([
      fetchJson(`/rankings/current?storefront=nutaku-all-games`),
      fetchJson(`/rankings/current?storefront=erolabs-home-ranking`),
      fetchJson(`/leaderboards?storefront=nutaku-all-games&view=${encodeURIComponent(selectedView)}&limit=20`),
      fetchJson(`/leaderboards?storefront=erolabs-home-ranking&view=${encodeURIComponent(selectedView)}&limit=20`),
    ]);

    selectedStorefrontLabel.textContent = "Nutaku All + EroLabs";
    captureDateLabel.textContent = `${formatDate(nutakuCompact.latest_date)} / ${formatDate(erolabsCompact.latest_date)}`;
    snapshotTypeLabel.innerHTML = `${homeHideImputed.checked ? "Filtered" : "Mixed"} snapshots`;

    if (homeHideImputed.checked && nutakuCurrent.data_source === "imputed" && erolabsCurrent.data_source === "imputed") {
      rankingSubtitle.textContent = "Both latest snapshots are imputed and hidden by filter";
      renderEmpty(nutakuBoard, "Nutaku current snapshot is imputed. Disable the filter to show the compact board.");
      renderEmpty(erolabsBoard, "EroLabs current snapshot is imputed. Disable the filter to show the compact board.");
      return;
    }

    rankingSubtitle.textContent =
      selectedView === "current"
        ? "Daily position with day-over-day movement"
        : selectedView === "avg7"
          ? "7-day average rank with movement vs previous 7-day window"
          : "30-day average rank with movement vs previous 30-day window";

    nutakuBoardSubtitle.textContent = `${formatDataSource(nutakuCurrent.data_source)} snapshot · top 20`;
    erolabsBoardSubtitle.textContent = `${formatDataSource(erolabsCurrent.data_source)} snapshot · top 20`;
    nutakuMoreLink.href = "/?storefront=nutaku-all-games";

    if (homeHideImputed.checked && nutakuCurrent.data_source === "imputed") {
      renderEmpty(nutakuBoard, "Nutaku latest snapshot is imputed. Disable the filter to show it.");
    } else {
      renderCompactBoard(nutakuBoard, nutakuCompact.entries, 20);
    }

    if (homeHideImputed.checked && erolabsCurrent.data_source === "imputed") {
      renderEmpty(erolabsBoard, "EroLabs latest snapshot is imputed. Disable the filter to show it.");
    } else {
      renderCompactBoard(erolabsBoard, erolabsCompact.entries, 20);
    }
  }

  homeHideImputed.addEventListener("change", loadCompactLeaderboards);
  homeViewSelector.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedView = button.dataset.view;
      homeViewSelector.querySelectorAll("button").forEach((node) => node.classList.remove("is-active"));
      button.classList.add("is-active");
      loadCompactLeaderboards();
    });
  });
  searchButton.addEventListener("click", runSearch);
  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") runSearch();
  });

  await loadCompactLeaderboards();
}

async function initGame() {
  const gameId = getQueryParam("id");
  const gameTitle = document.querySelector("#game-title");
  const gameSummary = document.querySelector("#game-summary");
  const gamePoster = document.querySelector("#game-poster");
  const gameDescription = document.querySelector("#game-description");
  const developerChip = document.querySelector("#developer-chip");
  const developerLabel = document.querySelector("#developer-label");
  const publisherChip = document.querySelector("#publisher-chip");
  const publisherLabel = document.querySelector("#publisher-label");
  const defaultStorefrontLabel = document.querySelector("#default-storefront-label");
  const gameLink = document.querySelector("#game-link");
  const gameLinkGrid = document.querySelector("#game-link-grid");
  const storefrontSelect = document.querySelector("#game-storefront-select");
  const bestRankLabel = document.querySelector("#best-rank-label");
  const observedAvgRankLabel = document.querySelector("#observed-avg-rank-label");
  const adjustedAvgRankLabel = document.querySelector("#adjusted-avg-rank-label");
  const firstSeenLabel = document.querySelector("#first-seen-label");
  const coverageLabel = document.querySelector("#coverage-label");
  const lastSeenLabel = document.querySelector("#last-seen-label");
  const historySummaryLabel = document.querySelector("#history-summary-label");
  const aliasesList = document.querySelector("#aliases-list");
  const historyChart = document.querySelector("#history-chart");
  const historyTable = document.querySelector("#history-table");
  const hideImputedToggle = document.querySelector("#hide-imputed-toggle");
  const rangeSelector = document.querySelector("#range-selector");

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
  const sourceCounts = history.history.reduce((acc, entry) => {
    acc[entry.data_source] = (acc[entry.data_source] || 0) + 1;
    return acc;
  }, {});
  const sourceSummary = Object.entries(sourceCounts)
    .map(([source, count]) => `${count} ${formatDataSource(source).toLowerCase()}`)
    .join(" · ");
  if (gamePoster) {
    if (summary.image_url) {
      gamePoster.innerHTML = `<img src="${summary.image_url}" alt="${summary.canonical_name}" />`;
    } else {
      gamePoster.textContent = summary.canonical_name
        .split(" ")
        .slice(0, 2)
        .map((part) => part[0] || "")
        .join("")
        .toUpperCase();
    }
  }
  if (developerLabel) developerLabel.textContent = summary.developer || "-";
  if (publisherLabel) publisherLabel.textContent = summary.publisher || "-";
  if (defaultStorefrontLabel) defaultStorefrontLabel.textContent = summary.default_storefront_name || "-";
  if (gameDescription) {
    if (summary.description) {
      gameDescription.hidden = false;
      gameDescription.textContent = summary.description;
    } else {
      gameDescription.hidden = true;
    }
  }
  if (gameLink) {
    if (summary.game_url) {
      gameLink.href = summary.game_url;
      gameLink.textContent = `Open ${summary.metadata_storefront_name || "game"} page`;
      gameLink.removeAttribute("aria-disabled");
    } else {
      gameLink.removeAttribute("href");
      gameLink.setAttribute("aria-disabled", "true");
      gameLink.textContent = "No game page available";
    }
  }
  if (bestRankLabel) bestRankLabel.textContent = summary.best_rank ?? "-";
  if (observedAvgRankLabel) observedAvgRankLabel.textContent = formatNumber(summary.observed_avg_rank_overall);
  if (adjustedAvgRankLabel) adjustedAvgRankLabel.textContent = formatNumber(summary.adjusted_avg_rank_overall);
  if (firstSeenLabel) firstSeenLabel.textContent = formatDate(summary.first_seen_date);
  if (coverageLabel) coverageLabel.textContent = formatPercent(summary.coverage_ratio);
  if (lastSeenLabel) lastSeenLabel.textContent = formatDate(summary.last_seen_date);

  aliasesList.innerHTML = summary.aliases.length
    ? summary.aliases
        .map(
          (alias) => `
            <article class="alias-item">
              <div>
                <strong>${alias.alias_title}</strong>
                <span>${alias.storefront_name}</span>
              </div>
              ${
                alias.url
                  ? `<a class="ranking-link" href="${alias.url}" target="_blank" rel="noreferrer">Open page</a>`
                  : `<span class="alias-item__muted">No link</span>`
              }
            </article>
          `
        )
        .join("")
    : `<div class="empty-state">No aliases recorded.</div>`;

  let selectedRange = "all";
  const storefrontOptions = summary.aliases.reduce((acc, alias) => {
    if (!acc.some((entry) => entry.slug === alias.storefront_slug)) {
      acc.push({ slug: alias.storefront_slug, name: alias.storefront_name });
    }
    return acc;
  }, []);
  const defaultStorefront =
    storefrontOptions.find((option) => option.slug === "nutaku-all-games")?.slug ||
    summary.default_storefront_slug ||
    storefrontOptions[0]?.slug ||
    "";
  let selectedStorefront = defaultStorefront;

  function metadataCompletenessScore(metadata) {
    if (!metadata) return -1;
    return [
      metadata.image_url,
      metadata.description,
      metadata.developer,
      metadata.publisher,
      metadata.url,
    ].filter(Boolean).length;
  }

  function resolveMetadataForStorefront(storefrontSlug) {
    const group = metadataGroupForStorefront(storefrontSlug);
    if (group === "nutaku") {
      const preferredNutakuOrder = ["nutaku-all-games", "nutaku-browser-ranking", "nutaku-mobile-ranking"];
      const candidates = preferredNutakuOrder
        .map((candidate, index) => ({
          metadata: summary.storefront_metadata?.[candidate] || null,
          priority: index,
        }))
        .filter((entry) => entry.metadata);

      candidates.sort((left, right) => {
        const scoreDelta = metadataCompletenessScore(right.metadata) - metadataCompletenessScore(left.metadata);
        if (scoreDelta !== 0) return scoreDelta;
        return left.priority - right.priority;
      });

      return candidates[0]?.metadata || null;
    }
    if (group === "erolabs") {
      return summary.storefront_metadata?.["erolabs-home-ranking"] || null;
    }
    return summary.storefront_metadata?.[storefrontSlug] || null;
  }

  if (storefrontSelect) {
    storefrontSelect.innerHTML = storefrontOptions
      .map(
        (option) => `
          <option value="${option.slug}" ${option.slug === defaultStorefront ? "selected" : ""}>${option.name}</option>
        `
      )
      .join("");
  }

  function renderStorefrontMetrics(storefrontSlug) {
    const metrics = summary.storefront_metrics?.find((entry) => entry.storefront_slug === storefrontSlug);
    if (!metrics) {
      if (bestRankLabel) bestRankLabel.textContent = summary.best_rank ?? "-";
      if (observedAvgRankLabel) observedAvgRankLabel.textContent = formatNumber(summary.observed_avg_rank_overall);
      if (adjustedAvgRankLabel) adjustedAvgRankLabel.textContent = formatNumber(summary.adjusted_avg_rank_overall);
      if (firstSeenLabel) firstSeenLabel.textContent = formatDate(summary.first_seen_date);
      if (coverageLabel) coverageLabel.textContent = formatPercent(summary.coverage_ratio);
      if (lastSeenLabel) lastSeenLabel.textContent = formatDate(summary.last_seen_date);
      return;
    }

    if (bestRankLabel) bestRankLabel.textContent = metrics.best_rank ?? "-";
    if (observedAvgRankLabel) observedAvgRankLabel.textContent = formatNumber(metrics.observed_avg_rank);
    if (adjustedAvgRankLabel) adjustedAvgRankLabel.textContent = formatNumber(metrics.adjusted_avg_rank);
    if (firstSeenLabel) firstSeenLabel.textContent = formatDate(metrics.first_seen_date);
    if (coverageLabel) coverageLabel.textContent = formatPercent(metrics.coverage_ratio);
    if (lastSeenLabel) lastSeenLabel.textContent = formatDate(metrics.last_seen_date);
  }

  function renderStorefrontMetadata(storefrontSlug) {
    const metadata = resolveMetadataForStorefront(storefrontSlug);

    const effectiveDeveloper = metadata?.developer || summary.developer || "-";
    const effectivePublisher = metadata?.publisher || null;
    const effectiveDescription = metadata?.description || summary.description || null;
    const effectiveImageUrl = metadata?.image_url || summary.image_url || null;
    const effectiveAliasTitle = metadata?.alias_title || summary.canonical_name;

    if (gamePoster) {
      if (effectiveImageUrl) {
        gamePoster.innerHTML = `<img src="${effectiveImageUrl}" alt="${effectiveAliasTitle}" />`;
      } else {
        gamePoster.textContent = summary.canonical_name
          .split(" ")
          .slice(0, 2)
          .map((part) => part[0] || "")
          .join("")
          .toUpperCase();
      }
    }

    if (developerLabel) developerLabel.textContent = effectiveDeveloper;
    if (publisherLabel) publisherLabel.textContent = effectivePublisher || "-";
    if (publisherChip) publisherChip.hidden = !effectivePublisher;
    if (developerChip) developerChip.hidden = false;

    if (gameDescription) {
      if (effectiveDescription) {
        gameDescription.hidden = false;
        gameDescription.textContent = effectiveDescription;
      } else {
        gameDescription.hidden = true;
      }
    }
  }

  function renderGameState() {
    const storefrontHistory = selectedStorefront
      ? history.history.filter((entry) => entry.storefront === selectedStorefront)
      : history.history;
    const filteredHistory = filterHistory(storefrontHistory, {
      hideImputed: hideImputedToggle.checked,
      range: selectedRange,
    });
    const windowAverage = computeWindowAverage(filteredHistory);
    const selectedStorefrontName =
      storefrontOptions.find((option) => option.slug === selectedStorefront)?.name || "Selected storefront";
    const selectedMetadata = resolveMetadataForStorefront(selectedStorefront);
    const metadataGroup = metadataGroupForStorefront(selectedStorefront);
    if (gameLinkGrid) {
      const alternateAliases = summary.aliases.filter(
        (alias) => alias.url && metadataGroupForStorefront(alias.storefront_slug) !== metadataGroup
      );
      gameLinkGrid.innerHTML = alternateAliases.length
        ? alternateAliases
            .map(
              (alias) => `
                <a class="storefront-link" href="${alias.url}" target="_blank" rel="noreferrer">
                  <span class="storefront-link__label">${alias.storefront_name}</span>
                  <strong>${alias.alias_title}</strong>
                </a>
              `
            )
            .join("")
        : "";
    }
    if (historySummaryLabel) {
      historySummaryLabel.textContent = `${selectedStorefrontName} · ${filteredHistory.length} visible points · lower rank is better`;
    }
    renderStorefrontMetrics(selectedStorefront);
    renderStorefrontMetadata(selectedStorefront);
    if (defaultStorefrontLabel) defaultStorefrontLabel.textContent = selectedStorefrontName;
    if (gameLink) {
      if (selectedMetadata?.url) {
        const metadataStorefrontName = selectedMetadata.storefront_name || selectedStorefrontName;
        gameLink.href = selectedMetadata.url;
        gameLink.textContent = `Open ${metadataStorefrontName} page`;
        gameLink.removeAttribute("aria-disabled");
      } else if (summary.game_url) {
        gameLink.href = summary.game_url;
        gameLink.textContent = `Open ${summary.metadata_storefront_name || "game"} page`;
        gameLink.removeAttribute("aria-disabled");
      } else {
        gameLink.removeAttribute("href");
        gameLink.setAttribute("aria-disabled", "true");
        gameLink.textContent = "No game page available";
      }
    }
    gameSummary.textContent = `${summary.ranking_points} ranking points collected across all tracked storefronts. ${sourceSummary}. Observed window avg rank: ${formatNumber(
      windowAverage
    )} on ${selectedStorefrontName}.`;

    renderHistoryChart(historyChart, filteredHistory);

    if (!filteredHistory.length) {
      renderEmpty(historyTable, "No historical rows available with the active filters.");
      return;
    }

    historyTable.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Storefront</th>
            <th>Rank</th>
            <th>Source</th>
            <th>Alias</th>
          </tr>
        </thead>
        <tbody>
          ${filteredHistory
            .map(
              (entry) => `
                <tr>
                  <td>${formatDate(entry.capture_date)}</td>
                  <td>${entry.storefront_name}</td>
                  <td>${entry.rank}</td>
                  <td><span class="${badgeClass(entry.data_source)}">${formatDataSource(entry.data_source)}</span></td>
                  <td>${entry.alias_title}</td>
                </tr>
              `
            )
            .join("")}
        </tbody>
      </table>
    `;
  }

  if (hideImputedToggle) {
    hideImputedToggle.addEventListener("change", renderGameState);
  }
  if (rangeSelector) {
    rangeSelector.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        selectedRange = button.dataset.range;
        rangeSelector.querySelectorAll("button").forEach((node) => node.classList.remove("is-active"));
        button.classList.add("is-active");
        renderGameState();
      });
    });
  }
  if (storefrontSelect) {
    storefrontSelect.addEventListener("change", () => {
      selectedStorefront = storefrontSelect.value;
      renderGameState();
    });
  }

  renderGameState();
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
