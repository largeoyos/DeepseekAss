const state = {
  token: sessionStorage.getItem("deepseekass_token") || "",
  user: null,
  currentBook: sessionStorage.getItem("deepseekass_book") || "",
  books: [],
  activeTab: "books",
  eventSource: null,
  streamBuffer: "",
};

const $ = (id) => document.getElementById(id);

function showToast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => el.classList.add("hidden"), 2600);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.detail || "请求失败");
  }
  return data;
}

function setAuthed(authed) {
  $("loginView").classList.toggle("hidden", authed);
  $("mainView").classList.toggle("hidden", !authed);
}

function selectTab(tab) {
  state.activeTab = tab;
  for (const panel of ["books", "write", "chapters", "tasks"]) {
    $(`${panel}Panel`).classList.toggle("hidden", panel !== tab);
  }
  document.querySelectorAll(".bottom-nav button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  if (tab === "chapters" && state.currentBook) loadChapters();
}

function setCurrentBook(title) {
  state.currentBook = title;
  sessionStorage.setItem("deepseekass_book", title);
  $("currentBookTitle").textContent = title || "书架";
}

async function bootstrap() {
  if (!state.token) {
    setAuthed(false);
    return;
  }
  try {
    const session = await api("/api/session");
    state.user = session.user;
    $("apiNotice").classList.toggle("hidden", session.api_configured);
    setAuthed(true);
    await loadBooks();
    if (state.currentBook) await loadMeta();
  } catch {
    state.token = "";
    sessionStorage.removeItem("deepseekass_token");
    setAuthed(false);
  }
}

async function loadBooks() {
  const data = await api("/api/books");
  state.books = data.books || [];
  renderBooks();
  if (!state.currentBook && state.books[0]) {
    setCurrentBook(state.books[0].title);
    await loadMeta();
  }
}

function renderBooks() {
  const list = $("bookList");
  if (!state.books.length) {
    list.innerHTML = `<div class="notice">还没有书。先创建一本，再到“写作”里填写设定。</div>`;
    return;
  }
  list.innerHTML = "";
  for (const book of state.books) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "book-card";
    item.innerHTML = `<span><strong></strong><small>点击进入写作台</small></span><span>${book.title === state.currentBook ? "当前" : "打开"}</span>`;
    item.querySelector("strong").textContent = book.title;
    item.addEventListener("click", async () => {
      setCurrentBook(book.title);
      renderBooks();
      await loadMeta();
      selectTab("write");
    });
    list.appendChild(item);
  }
}

async function loadMeta() {
  if (!state.currentBook) return;
  const data = await api(`/api/books/${encodeURIComponent(state.currentBook)}/meta`);
  const meta = data.meta || {};
  $("metaProtagonist").value = meta.protagonist_bio || "";
  $("metaBackground").value = meta.background_story || "";
  $("metaDemand").value = meta.writing_demand || "";
  $("metaPlan").value = meta.author_plan || "";
  $("metaGenre").value = meta.genre || "";
  $("metaTone").value = meta.style_tone || "";
  setCurrentBook(meta.title || state.currentBook);
}

async function saveMeta() {
  requireBook();
  await api(`/api/books/${encodeURIComponent(state.currentBook)}/meta`, {
    method: "PUT",
    body: JSON.stringify({
      protagonist_bio: $("metaProtagonist").value,
      background_story: $("metaBackground").value,
      writing_demand: $("metaDemand").value,
      author_plan: $("metaPlan").value,
      genre: $("metaGenre").value,
      style_tone: $("metaTone").value,
    }),
  });
  showToast("设定已保存");
}

async function loadChapters() {
  if (!state.currentBook) return;
  const data = await api(`/api/books/${encodeURIComponent(state.currentBook)}/chapters`);
  const chapters = data.chapters || [];
  const list = $("chapterList");
  if (!chapters.length) {
    list.innerHTML = `<div class="notice">暂无章节。到“写作”里生成下一章。</div>`;
    $("reader").classList.add("hidden");
    return;
  }
  list.innerHTML = "";
  for (const chapter of chapters) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "chapter-item";
    item.innerHTML = `<span><strong></strong><small></small></span><span>阅读</span>`;
    item.querySelector("strong").textContent = chapter.title || `第${chapter.chapter_num}章`;
    item.querySelector("small").textContent = `第 ${chapter.chapter_num} 章 · v${chapter.active || chapter.version || 1}`;
    item.addEventListener("click", () => readChapter(chapter.chapter_num));
    list.appendChild(item);
  }
}

async function readChapter(chapterNum) {
  const data = await api(`/api/books/${encodeURIComponent(state.currentBook)}/chapters/${chapterNum}`);
  const chapter = data.chapter || {};
  $("readerTitle").textContent = chapter.title || `第${chapterNum}章`;
  $("readerContent").textContent = data.content || "";
  $("reader").classList.remove("hidden");
}

async function startGeneration() {
  requireBook();
  await saveMeta();
  const data = await api(`/api/books/${encodeURIComponent(state.currentBook)}/generate`, {
    method: "POST",
    body: JSON.stringify({
      chapter_title: $("genTitle").value,
      plot: $("genPlot").value,
      target_words: Number($("genWords").value || 3000),
    }),
  });
  state.streamBuffer = "";
  $("streamText").textContent = "";
  connectTask(data.task_id);
  selectTab("tasks");
  showToast("生成任务已提交");
}

function connectTask(taskId) {
  if (state.eventSource) {
    state.eventSource.close();
  }
  const url = `/api/tasks/${encodeURIComponent(taskId)}/events?token=${encodeURIComponent(state.token)}`;
  state.eventSource = new EventSource(url);
  const handle = (event) => {
    const payload = JSON.parse(event.data);
    updateTask(payload);
  };
  for (const type of ["started", "progress", "completed", "failed", "cancelled", "finished"]) {
    state.eventSource.addEventListener(type, handle);
  }
  state.eventSource.onerror = () => {
    $("taskMessage").textContent = "进度连接中断，可刷新任务页查看结果。";
  };
}

function updateTask(payload) {
  const data = payload.data || {};
  const percent = Number(data.progress || (payload.type === "completed" ? 100 : 0));
  const stage = data.stage || payload.type;
  $("taskStage").textContent = stage;
  $("taskPercent").textContent = `${percent}%`;
  $("taskBar").style.width = `${percent}%`;
  $("taskMessage").textContent = payload.message || "";
  if (data.text) {
    state.streamBuffer = `${state.streamBuffer}${data.text}`.slice(-8000);
    $("streamText").textContent = state.streamBuffer;
  }
  if (data.result && data.result.preview) {
    $("streamText").textContent = data.result.preview;
  }
  if (["completed", "failed", "cancelled"].includes(payload.type) && state.eventSource) {
    state.eventSource.close();
    if (payload.type === "completed") {
      loadChapters();
      showToast("章节已生成");
    }
  }
}

function requireBook() {
  if (!state.currentBook) {
    throw new Error("请先选择或创建一本书");
  }
}

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("loginError").textContent = "";
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("loginUsername").value,
        password: $("loginPassword").value,
      }),
    });
    state.token = data.token;
    sessionStorage.setItem("deepseekass_token", state.token);
    await bootstrap();
  } catch (error) {
    $("loginError").textContent = error.message;
  }
});

$("logoutBtn").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
  } catch {
    // Local session cleanup is enough if the network request fails.
  }
  state.token = "";
  state.currentBook = "";
  sessionStorage.removeItem("deepseekass_token");
  sessionStorage.removeItem("deepseekass_book");
  setAuthed(false);
});

$("createBookForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = $("newBookTitle").value.trim();
  if (!title) return;
  try {
    await api("/api/books", { method: "POST", body: JSON.stringify({ title }) });
    $("newBookTitle").value = "";
    setCurrentBook(title);
    await loadBooks();
    await loadMeta();
    selectTab("write");
  } catch (error) {
    showToast(error.message);
  }
});

$("saveMetaBtn").addEventListener("click", () => saveMeta().catch((error) => showToast(error.message)));
$("generateBtn").addEventListener("click", () => startGeneration().catch((error) => showToast(error.message)));
$("refreshChaptersBtn").addEventListener("click", () => loadChapters().catch((error) => showToast(error.message)));

document.querySelectorAll(".bottom-nav button").forEach((btn) => {
  btn.addEventListener("click", () => selectTab(btn.dataset.tab));
});

bootstrap();
