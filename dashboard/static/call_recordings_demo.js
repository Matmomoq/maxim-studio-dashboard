const recordingNumber = new Intl.NumberFormat("ru-RU");

function recordingEscape(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function recordingDuration(seconds) {
  const minutes = Math.floor(Number(seconds || 0) / 60);
  const rest = Number(seconds || 0) % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function recordingMessage(text = "") {
  const banner = document.querySelector("#recordingsError");
  banner.hidden = !text;
  banner.textContent = text;
}

function renderRecordingRows(rows) {
  const body = document.querySelector("#recordingsRows");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="registry-empty">Подходящие звонки UIS не найдены</td></tr>';
    return;
  }
  body.innerHTML = rows.map((row) => `<tr>
    <td class="recording-deal-cell"><a href="${recordingEscape(row.lead_url)}" target="_blank" rel="noopener">Сделка №${recordingEscape(row.lead_id)}</a><strong>${recordingEscape(row.lead_name)}</strong><small>Закрыта: ${recordingEscape(row.closed_at)}</small></td>
    <td><strong>${recordingEscape(row.occurred_at)}</strong><small>${recordingEscape(row.direction)}</small></td>
    <td><strong>${recordingEscape(row.employee)}</strong><small>${recordingEscape(row.phone)}</small></td>
    <td><span class="recording-reason">${recordingEscape(row.loss_reason)}</span></td>
    <td>${recordingEscape(row.provider)}</td>
    <td class="number"><strong>${recordingDuration(row.duration_sec)}</strong></td>
    <td class="recording-player-cell"><audio controls preload="none" src="${recordingEscape(row.audio_url)}" aria-label="Запись звонка по сделке ${recordingEscape(row.lead_id)}"></audio><small class="recording-player-status">Запись загружается только при нажатии</small></td>
  </tr>`).join("");
  body.querySelectorAll("audio").forEach((audio) => {
    audio.addEventListener("playing", () => {
      body.querySelectorAll("audio").forEach((other) => { if (other !== audio) other.pause(); });
      audio.nextElementSibling.textContent = "Воспроизводится через защищённый маршрут";
    });
    audio.addEventListener("error", () => {
      audio.nextElementSibling.textContent = "Не удалось открыть запись";
      audio.closest("tr").classList.add("recording-error-row");
    });
  });
}

async function loadRecordingDemo() {
  document.querySelector("#recordingsLoading").classList.add("active");
  recordingMessage();
  try {
    const response = await fetch("/api/call-recordings-demo");
    if (response.status === 401) {
      window.location.assign("/login?next=/call-recordings-demo");
      return;
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Не удалось получить тестовые звонки");
    document.querySelector("#recordingsShown").textContent = recordingNumber.format(data.shown);
    document.querySelector("#recordingsEligible").textContent = recordingNumber.format(data.eligible_calls);
    document.querySelector("#recordingsDeals").textContent = recordingNumber.format(data.eligible_leads);
    document.querySelector("#recordingsScope").textContent = `${data.provider} · ${data.loss_reason}`;
    renderRecordingRows(data.calls);
  } catch (error) {
    recordingMessage(error.message);
    renderRecordingRows([]);
  } finally {
    document.querySelector("#recordingsLoading").classList.remove("active");
  }
}

document.addEventListener("DOMContentLoaded", loadRecordingDemo);
