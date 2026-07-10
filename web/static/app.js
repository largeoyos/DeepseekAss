const state = {
  token: sessionStorage.getItem("deepseekass_token") || "",
  currentBook: sessionStorage.getItem("deepseekass_book") || "",
  section: "write",
  workspace: "books",
  books: [],
  chapters: [],
  tree: {nodes: [], active_path: []},
  chapterGraphZoom: 1,
  selectedNodeId: "",
  selectedChapterNum: 0,
  selectedVersion: 0,
  world: {},
  worldCategory: "characters",
  worldIndex: -1,
  worldEntityEditable: true,
  sections: [],
  contFiles: [],
  selectedSectionIndex: -1,
  continuationTab: "source",
  selectedContinuationRun: null,
  snapshots: [],
  selectedSnapshotId: "",
  sensitiveTicket: "",
  eventSource: null,
  streamBuffer: "",
  lastAgentPlanId: "",
  lastPolishPlanId: "",
  lastExtraPlanId: "",
  lastAdvisorResult: null,
  agentAdvice: [],
  advisorHistory: [],
  selectedAdvisorHistoryIndex: null,
  pendingChanges: [],
  pendingWorldMaintenance: [],
  agentProfiles: [],
  agentSessions: [],
  selectedAgentSessionId: "",
  activeAgentRunId: "",
  selectedChangeSetId: "",
  downloads: [],
  roleBook: {profiles: [], memories: []},
  selectedRoleIds: [],
  currentConversationId: "",
  currentConversationRecord: {},
  chatMessages: [],
  senderProfiles: [],
  scenePresets: [],
  memoryChangeSets: [],
  selectedMemoryChangeId: "",
  chatBranches: [],
  activeChatBranchId: "",
  selectedChatMessageId: "",
  presets: {},
  defaultPresetNames: [],
  selectedNotePath: "",
  selectedNoteType: "file",
  worldPolicyEntities: []
};
const $ = (id) => document.getElementById(id);
const enc = encodeURIComponent;
const worldCategories = [
  ["characters", "角色"], ["locations", "地点"], ["timeline", "时间线"],
  ["active_plot_threads", "剧情线"], ["world_rules", "规则"],
  ["key_worldbuilding_passages", "关键设定"], ["global_foreshadowing", "伏笔"],
  ["global_key_dialogues", "关键对白"], ["facts", "事实"], ["manual_overrides", "手动覆盖"],
  ["chapter_snapshots", "章节快照"], ["duplicate_candidates", "重复候选"],
  ["merge_history", "合并历史"], ["diagnostics", "诊断"], ["migration_info", "迁移信息"]
];
function toast(msg){ const el=$("toast"); el.textContent=msg; el.classList.remove("hidden"); clearTimeout(toast.t); toast.t=setTimeout(()=>el.classList.add("hidden"),2600); }
async function api(path, options={}){ const headers={...(options.headers||{})}; if(!(options.body instanceof FormData)) headers["Content-Type"]="application/json"; if(state.token) headers.Authorization=`Bearer ${state.token}`; const res=await fetch(path,{...options,headers}); const text=await res.text(); let data={}; try{ data=text?JSON.parse(text):{}; }catch{ data={raw:text}; } if(!res.ok) throw new Error(data.detail||"请求失败"); return data; }
function setAuthed(v){ $("loginView").classList.toggle("hidden",v); $("mainView").classList.toggle("hidden",!v); }
function requireBook(){ if(!state.currentBook) throw new Error("请先选择或创建一本书"); }
function setCurrentBook(title){ state.currentBook=title||""; if(title) sessionStorage.setItem("deepseekass_book",title); $("currentBookTitle").textContent=title||"书架"; const contSelect=$("contBookSelect"); if(contSelect) contSelect.value=title||""; const contTitle=$("contAnalysisTitle"); if(title && contTitle && !contTitle.value.trim()) contTitle.value=title; }
function renderCards(el, items, renderer){ el.innerHTML=""; if(!items.length){ el.innerHTML='<div class="notice small">暂无数据</div>'; return; } for(const item of items) el.appendChild(renderer(item)); }
function buttonCard(title, sub, action, active=false){ const b=document.createElement("button"); b.type="button"; b.className=`item-card${active?" active":""}`; b.innerHTML=`<span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(sub||"")}</small></span><span>${escapeHtml(action||"")}</span>`; return b; }
function escapeHtml(v){ return String(v??"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c])); }
function selectSection(section){ state.section=section; for(const name of ["write","continuation","chat","notes","settings","tokens","tasks"]){ $(`${name}Panel`).classList.toggle("hidden", name!==section); } document.querySelectorAll(".rail-nav button").forEach(b=>b.classList.toggle("active",b.dataset.section===section)); if(section==="continuation"){ loadContinuationProject().catch(e=>toast(e.message)); loadContinuationRuns().catch(()=>{}); } if(section==="chat") loadRoles().catch(e=>toast(e.message)); if(section==="notes") loadNoteTree().catch(e=>toast(e.message)); if(section==="settings") loadSettings().catch(e=>toast(e.message)); if(section==="tokens") loadTokens().catch(e=>toast(e.message)); if(section==="tasks") loadTasks().catch(e=>toast(e.message)); }
function selectWorkspace(name){ state.workspace=name; for(const n of ["books","chapters","world","agent","snapshots"]){ $(`${n}Workspace`).classList.toggle("hidden", n!==name); } document.querySelectorAll(".workspace-tabs button").forEach(b=>b.classList.toggle("active",b.dataset.workspace===name)); if(name==="chapters") loadChapters().catch(e=>toast(e.message)); if(name==="world") loadWorld().catch(e=>toast(e.message)); if(name==="agent") loadAgentState().catch(e=>toast(e.message)); if(name==="snapshots") loadSnapshots().catch(e=>toast(e.message)); }
async function bootstrap(){ if(!state.token){ setAuthed(false); return; } try{ const s=await api("/api/session"); $("railUser").textContent=(s.user||{}).username||"Web"; $("apiNotice").classList.toggle("hidden",s.api_configured); setAuthed(true); await loadBooks(); if(state.currentBook) await loadMeta(); }catch{ state.token=""; sessionStorage.removeItem("deepseekass_token"); setAuthed(false); } }
async function loadBooks(){ const data=await api("/api/books"); state.books=data.books||[]; if(state.currentBook && !state.books.some(book=>book.title===state.currentBook)){ setCurrentBook(""); } renderBooks(); if(!state.currentBook && state.books[0]){ setCurrentBook(state.books[0].title); await loadMeta(); } }
function renderBooks(){ renderCards($("bookList"), state.books, book=>{ const b=buttonCard(book.title, book.title===state.currentBook?"当前书籍":"点击切换", "打开", book.title===state.currentBook); b.onclick=async()=>{ setCurrentBook(book.title); renderBooks(); await loadMeta(); };
return b; }); }
async function renameCurrentBook(){ requireBook(); const next=(prompt("新书名", state.currentBook)||"").trim(); if(!next||next===state.currentBook) return; const data=await api(`/api/books/${enc(state.currentBook)}`,{method:"PATCH",body:JSON.stringify({new_title:next})}); setCurrentBook(data.title||next); await loadBooks(); await loadMeta(); toast("书籍已重命名"); }
async function deleteCurrentBook(){ requireBook(); if(!confirm(`删除小说「${state.currentBook}」及其所有章节、世界书和快照？此操作不可恢复。`)) return; const old=state.currentBook; await api(`/api/books/${enc(old)}`,{method:"DELETE"}); setCurrentBook(""); state.selectedNodeId=""; state.selectedChapterNum=0; state.selectedVersion=0; await loadBooks(); if(!state.currentBook){ $("readerTitle").textContent="未选择章节"; $("readerContent").textContent=""; } toast("书籍已删除"); }
async function loadMeta(){ if(!state.currentBook) return; const data=await api(`/api/books/${enc(state.currentBook)}/meta`); const m=data.meta||{}; setCurrentBook(m.title||state.currentBook); $("metaProtagonist").value=m.protagonist_bio||""; $("metaBackground").value=m.background_story||""; $("metaDemand").value=m.writing_demand||""; $("metaPlan").value=m.author_plan||""; $("metaGenre").value=m.genre||""; $("metaTone").value=m.style_tone||""; $("metaXpMode").checked=!!m.xp_mode; applyContinuationMeta(m); renderContinuationBookSelect(); }
function setValueIfPresent(id, value){ const el=$(id); if(el) el.value=value??""; }
function setCheckedIfPresent(id, value){ const el=$(id); if(el) el.checked=!!value; }
function syncContinuationXp(value){ setCheckedIfPresent("contMetaXpMode", value); setCheckedIfPresent("contAnalyzeXpMode", value); setCheckedIfPresent("contXpMode", value); }
function applyContinuationMeta(meta={}){ setValueIfPresent("contMetaProtagonist", meta.protagonist_bio||""); setValueIfPresent("contMetaBackground", meta.background_story||""); setValueIfPresent("contMetaDemand", meta.writing_demand||""); setValueIfPresent("contMetaPlan", meta.author_plan||""); setValueIfPresent("contMetaGenre", meta.genre||""); setValueIfPresent("contMetaTone", meta.style_tone||""); syncContinuationXp(!!meta.xp_mode); }
function renderContinuationBookSelect(){ const select=$("contBookSelect"); if(!select) return; const current=state.currentBook||""; select.innerHTML='<option value="">未选择书籍</option>'+(state.books||[]).map(book=>`<option value="${escapeHtml(book.title)}">${escapeHtml(book.title)}</option>`).join(""); select.value=current; }
async function loadContinuationProject(){ await loadBooks(); renderContinuationBookSelect(); if(state.currentBook) await loadContinuationMeta(false); await updateContinuationChapterInfo(); }
async function loadContinuationMeta(showToast=true){ requireBook(); const data=await api(`/api/books/${enc(state.currentBook)}/meta`); applyContinuationMeta(data.meta||{}); renderContinuationBookSelect(); await updateContinuationChapterInfo(); if(showToast) toast("续写设定已加载"); }
async function updateContinuationChapterInfo(){ const el=$("contChapterInfo"); if(!el) return; if(!state.currentBook){ el.textContent="尚未选择小说"; return; } try{ const data=await api(`/api/books/${enc(state.currentBook)}/chapter-tree`); const target=data.target||{}; const chapterNum=target.chapter_num||1; const parent=target.parent_id?`父节点 ${target.parent_id}`:"主线起点"; const activeCount=(data.active_path||[]).length; el.textContent=`下一章：第 ${chapterNum} 章 · ${parent} · 活跃路径 ${activeCount} 节点`; const titleInput=$("contChapterTitle"); if(titleInput && !titleInput.value.trim()) titleInput.placeholder=`留空自动生成第 ${chapterNum} 章`; }catch(e){ el.textContent=`章节目标读取失败：${e.message}`; } }
function collectContinuationMeta(){ return {protagonist_bio:$("contMetaProtagonist")?.value||"",background_story:$("contMetaBackground")?.value||"",writing_demand:$("contMetaDemand")?.value||"",author_plan:$("contMetaPlan")?.value||"",genre:$("contMetaGenre")?.value||"",style_tone:$("contMetaTone")?.value||"",xp_mode:$("contMetaXpMode")?.checked||false}; }
async function saveContinuationMeta(showToast=true){ requireBook(); await api(`/api/books/${enc(state.currentBook)}/meta`,{method:"PUT",body:JSON.stringify(collectContinuationMeta())}); await loadMeta(); if(showToast) toast("续写设定已保存"); }
async function selectContinuationBook(){ const title=$("contBookSelect")?.value||""; if(!title) return; setCurrentBook(title); setValueIfPresent("contAnalysisTitle", title); renderBooks(); await loadMeta(); await loadContinuationRuns().catch(()=>{}); }
async function createContinuationBook(){ const title=($("contNewBookTitle")?.value||$("contAnalysisTitle")?.value||"").trim(); if(!title) throw new Error("请输入新书名"); await api("/api/books",{method:"POST",body:JSON.stringify({title})}); setCurrentBook(title); setValueIfPresent("contAnalysisTitle", title); setValueIfPresent("contNewBookTitle", ""); await loadBooks(); await loadMeta(); toast("续写书籍已创建"); }
async function renameContinuationBook(){ await renameCurrentBook(); renderContinuationBookSelect(); setValueIfPresent("contAnalysisTitle", state.currentBook||""); }
async function deleteContinuationBook(){ await deleteCurrentBook(); renderContinuationBookSelect(); applyContinuationMeta({}); setValueIfPresent("contAnalysisTitle", state.currentBook||""); }
function openContinuationWorkspace(name){ selectSection("write"); selectWorkspace(name); }
async function saveMeta(){ requireBook(); await api(`/api/books/${enc(state.currentBook)}/meta`,{method:"PUT",body:JSON.stringify({protagonist_bio:$("metaProtagonist").value,background_story:$("metaBackground").value,writing_demand:$("metaDemand").value,author_plan:$("metaPlan").value,genre:$("metaGenre").value,style_tone:$("metaTone").value,xp_mode:$("metaXpMode").checked})}); toast("设定已保存"); }
async function contextPreview(){ requireBook(); const data=await api(`/api/books/${enc(state.currentBook)}/context-preview?chapter_title=${enc($("genTitle").value)}&plot=${enc($("genPlot").value)}`); $("streamText").textContent=data.content||data.preview||""; selectSection("tasks"); }
async function startGeneration(){ requireBook(); await saveMeta(); const data=await api(`/api/books/${enc(state.currentBook)}/generate`,{method:"POST",body:JSON.stringify({chapter_title:$("genTitle").value,plot:$("genPlot").value,target_words:Number($("genWords").value||3000)})}); connectTask(data.task_id); selectSection("tasks"); }
async function loadChapters(){ if(!state.currentBook) return; const [chapters, tree]=await Promise.all([api(`/api/books/${enc(state.currentBook)}/chapters`), api(`/api/books/${enc(state.currentBook)}/chapter-tree`)]); state.chapters=chapters.chapters||[]; state.tree=tree; renderChapterList(); renderTreeSelect(); renderTreeList(); }
function renderChapterList(){ renderCards($("chapterList"), state.chapters, ch=>{ const b=buttonCard(ch.title||`第${ch.chapter_num}章`, `第 ${ch.chapter_num} 章 · v${ch.active||ch.version||1}`, "阅读", ch.chapter_num===state.selectedChapterNum); b.onclick=()=>readChapter(ch.chapter_num); return b; }); }
function renderTreeSelect(){ const sel=$("treeSelect"); if(!sel) return; const trees=state.tree.trees||[]; sel.innerHTML=""; for(const tree of trees){ const opt=document.createElement("option"); opt.value=tree.tree_id; opt.textContent=`${tree.title||tree.tree_id}${tree.is_primary_tree?"":" · "+(tree.tree_kind||"")}`; opt.selected=tree.tree_id===state.tree.active_tree_id; sel.appendChild(opt); } }
async function switchChapterTree(){ requireBook(); const treeId=$("treeSelect").value; if(!treeId) return; const data=await api(`/api/books/${enc(state.currentBook)}/chapter-trees/${enc(treeId)}/activate`,{method:"POST",body:"{}"}); state.tree={...state.tree,...data}; state.selectedNodeId=""; state.selectedChapterNum=0; state.selectedVersion=0; renderTreeSelect(); renderTreeList(); toast("阅读树已切换"); }
function activeTreeNodes(){ const all=state.tree.nodes||[]; const activeTreeId=state.tree.active_tree_id||""; const scoped=all.filter(n=>!activeTreeId || !n.tree_id || n.tree_id===activeTreeId); return scoped.length?scoped:all; }
function chapterNodeLabel(n){ return n.virtual?"第零章":(n.display_label||n.title||n.id||"节点"); }
function renderTreeList(){ const nodes=activeTreeNodes().filter(n=>!n.virtual); const byId=Object.fromEntries(nodes.map(n=>[n.id,n])); function depth(n){ let d=0, p=n.parent_id; while(p && byId[p] && d<20){ d++; p=byId[p].parent_id; } return d; } renderCards($("treeList"), nodes, n=>{ const b=buttonCard(`${n.display_label||n.title||n.id}`, `${n.id}${(state.tree.active_path||[]).includes(n.id)?" · 活跃":""}`, "打开", n.id===state.selectedNodeId); b.className=`tree-node${n.id===state.selectedNodeId?" active":""}`; b.style.setProperty("--depth", depth(n)); b.querySelector("strong").insertAdjacentHTML("afterbegin", '<span class="tree-indent"></span>'); b.onclick=()=>readNode(n.id); return b; }); renderChapterGraph(); }
function renderChapterGraph(){ const box=$("chapterGraph"); if(!box) return; const nodes=activeTreeNodes(); if(!nodes.length){ box.innerHTML='<div class="notice small">暂无章节节点</div>'; return; } const byId=Object.fromEntries(nodes.map(n=>[String(n.id),n])); const children=new Map(); for(const n of nodes){ const pid=String(n.parent_id||""); if(pid && byId[pid]){ if(!children.has(pid)) children.set(pid,[]); children.get(pid).push(n); } } const roots=nodes.filter(n=>!n.parent_id || !byId[String(n.parent_id)]); if(!roots.length) roots.push(nodes[0]); const levels=[]; const seen=new Set(); const queue=roots.map(node=>({node,depth:0})); while(queue.length){ const item=queue.shift(); const node=item.node; const depth=item.depth; const id=String(node.id); if(seen.has(id)) continue; seen.add(id); if(!levels[depth]) levels[depth]=[]; levels[depth].push(node); for(const child of children.get(id)||[]) queue.push({node:child,depth:depth+1}); } for(const n of nodes){ if(!seen.has(String(n.id))){ if(!levels[0]) levels[0]=[]; levels[0].push(n); } } const nodeW=132, nodeH=54, gapX=28, gapY=70, pad=28; const maxCols=Math.max(1,...levels.map(l=>l.length)); const width=Math.max(320,pad*2+maxCols*nodeW+(maxCols-1)*gapX); const height=Math.max(180,pad*2+levels.length*nodeH+(levels.length-1)*gapY); const pos={}; levels.forEach((level,d)=>{ const total=level.length*nodeW+(level.length-1)*gapX; const start=(width-total)/2; level.forEach((n,i)=>{ pos[String(n.id)]={x:start+i*(nodeW+gapX),y:pad+d*(nodeH+gapY)}; }); }); const active=new Set(state.tree.active_path||[]); const selected=String(state.selectedNodeId||""); const zoom=Number(state.chapterGraphZoom||1); const edges=[]; for(const n of nodes){ const pid=String(n.parent_id||""); const id=String(n.id); if(pid && pos[pid] && pos[id]){ const isActive=active.has(pid)&&active.has(id); edges.push(`<path class="chapter-edge ${isActive?"active":""}" d="M ${pos[pid].x+nodeW/2} ${pos[pid].y+nodeH} C ${pos[pid].x+nodeW/2} ${pos[pid].y+32+nodeH}, ${pos[id].x+nodeW/2} ${pos[id].y-32}, ${pos[id].x+nodeW/2} ${pos[id].y}"/>`); } } const items=nodes.map(n=>{ const id=String(n.id); const p=pos[id]; if(!p) return ""; const isActive=active.has(id); const isSelected=selected===id; const label=escapeHtml(String(chapterNodeLabel(n)).slice(0,32)); const title=escapeHtml(String(n.title||n.display_label||label).slice(0,36)); const meta=n.virtual?"起点":`第${n.chapter_num||"?"}章 · v${n.version||"?"}`; return `<g class="chapter-graph-node ${isActive?"active":""} ${isSelected?"selected":""} ${n.virtual?"virtual":""}" data-node-id="${escapeHtml(id)}" transform="translate(${p.x},${p.y})"><rect width="${nodeW}" height="${nodeH}" rx="8"></rect><text x="12" y="22">${label}</text><text class="meta" x="12" y="40">${escapeHtml(meta)}</text><title>${title||label}</title></g>`; }).join(""); box.innerHTML=`<svg viewBox="0 0 ${width} ${height}" width="${Math.round(width*zoom)}" height="${Math.round(height*zoom)}" role="img" aria-label="章节图形树"><g>${edges.join("")}${items}</g></svg>`; box.querySelectorAll("[data-node-id]").forEach(el=>{ el.onclick=()=>selectGraphNode(el.dataset.nodeId); }); }
function selectGraphNode(id){ const node=(state.tree.nodes||[]).find(n=>String(n.id)===String(id)); if(!node) return; if(node.virtual){ state.selectedNodeId=id; state.selectedChapterNum=0; state.selectedVersion=0; $("readerTitle").textContent=node.display_label||"第零章"; $("readerContent").textContent="故事起点。点击切换分支可将活跃路径重置到该节点。"; $("chapterEditTitle").value=node.title||""; $("chapterEditText").value=""; $("chapterSummaryText").value=node.summary||""; renderTreeList(); return; } readNode(id).catch(e=>toast(e.message)); }
function changeChapterGraphZoom(delta){ state.chapterGraphZoom=Math.min(1.8,Math.max(0.55,Number(state.chapterGraphZoom||1)+delta)); renderChapterGraph(); }
function resetChapterGraphZoom(){ state.chapterGraphZoom=1; renderChapterGraph(); }
function fitChapterGraph(){ const box=$("chapterGraph"); const svg=box?box.querySelector("svg"):null; if(!box||!svg) return; const view=(svg.getAttribute("viewBox")||"").split(/\s+/).map(Number); const width=view[2]||0; if(width>0) state.chapterGraphZoom=Math.min(1.4,Math.max(0.55,(box.clientWidth-24)/width)); renderChapterGraph(); }
async function readChapter(num){ const data=await api(`/api/books/${enc(state.currentBook)}/chapters/${num}`); const nodeId=(data.chapter||{}).node_id||""; if(nodeId){ await readNode(nodeId); return; } state.selectedChapterNum=num; state.selectedNodeId=""; state.selectedVersion=Number((data.chapter||{}).active||(data.chapter||{}).version||0); $("readerTitle").textContent=(data.chapter||{}).title||`第${num}章`; $("readerContent").textContent=data.content||""; $("chapterEditTitle").value=(data.chapter||{}).title||""; $("chapterEditText").value=data.content||""; $("chapterSummaryText").value=""; await loadVersions(num); renderChapterList(); }
async function readNode(id){ const data=await api(`/api/books/${enc(state.currentBook)}/nodes/${enc(id)}`); state.selectedNodeId=id; const n=data.node||{}; state.selectedChapterNum=Number(n.chapter_num||0); state.selectedVersion=Number(n.version||0); $("readerTitle").textContent=n.display_label||n.title||id; $("readerContent").textContent=data.content||""; $("chapterEditTitle").value=n.title||n.display_label||""; $("chapterEditText").value=data.content||""; $("chapterSummaryText").value=n.summary||""; $("polishNodeId").value=id; $("extraStartNodeId").value=id; $("extraRefNodeId").value=id; if(state.selectedChapterNum) await loadVersions(state.selectedChapterNum); renderChapterList(); renderTreeList(); }
async function loadVersions(num){ const data=await api(`/api/books/${enc(state.currentBook)}/chapters/${num}/versions`); renderCards($("versionList"), data.versions||[], v=>{ const active=v.v===data.active; const b=buttonCard(`v${v.v}`, active?"活跃版本":(v.created_at||""), active?"当前":"设为活跃", active); b.onclick=async()=>{ await api(`/api/books/${enc(state.currentBook)}/chapters/${num}/versions/${v.v}/activate`,{method:"POST",body:"{}"}); toast("活跃版本已切换"); await loadChapters(); await readChapter(num); };
return b; }); }
async function switchSelectedBranch(){ requireBook(); if(!state.selectedNodeId) throw new Error("请先选择章节树节点"); await api(`/api/books/${enc(state.currentBook)}/nodes/${enc(state.selectedNodeId)}/activate`,{method:"POST",body:"{}"}); toast("活跃分支已切换"); await loadChapters(); await readNode(state.selectedNodeId); }
async function saveChapterContent(){ requireBook(); if(!state.selectedNodeId) throw new Error("请先选择章节树节点"); const body={title:$("chapterEditTitle").value,content:$("chapterEditText").value,activate:true}; const data=await api(`/api/books/${enc(state.currentBook)}/nodes/${enc(state.selectedNodeId)}/content`,{method:"PUT",body:JSON.stringify(body)}); toast(`已保存为 v${data.version}`); await loadChapters(); await readNode(data.node_id); }
async function saveNodeSummary(){ requireBook(); if(!state.selectedNodeId) throw new Error("请先选择章节树节点"); const data=await api(`/api/books/${enc(state.currentBook)}/nodes/${enc(state.selectedNodeId)}/summary`,{method:"PUT",body:JSON.stringify({summary:$("chapterSummaryText").value})}); toast("节点摘要已保存"); await loadChapters(); if(data.node) await readNode(data.node.id); }
async function deleteSelectedNode(){ requireBook(); if(!state.selectedNodeId) throw new Error("请先选择章节树节点"); await api(`/api/books/${enc(state.currentBook)}/nodes/${enc(state.selectedNodeId)}`,{method:"DELETE"}); toast("子树已删除"); state.selectedNodeId=""; state.selectedChapterNum=0; state.selectedVersion=0; await loadChapters(); }
async function deleteSelectedVersion(){ requireBook(); if(!state.selectedChapterNum || !state.selectedVersion) throw new Error("请先选择章节版本"); await api(`/api/books/${enc(state.currentBook)}/chapters/${state.selectedChapterNum}/versions/${state.selectedVersion}`,{method:"DELETE"}); toast("版本已删除"); state.selectedNodeId=""; state.selectedVersion=0; await loadChapters(); }
function setChapterInspector(title, html){ const titleEl=$("chapterInspectorTitle"); const box=$("chapterInspector"); if(titleEl) titleEl.textContent=title||"章节诊断"; if(box) box.innerHTML=html||'<div class="notice small">暂无内容</div>'; }
function clearChapterInspector(){ setChapterInspector("章节诊断", '<div class="notice small">查看活跃路径、节点路径或生成记录时在这里显示。</div>'); }
function chapterPathHtml(nodes=[]){ if(!nodes.length) return '<div class="notice small">没有路径节点。</div>'; const active=new Set(state.tree.active_path||[]); return `<div class="chapter-path-list">${nodes.map((n,i)=>`<article class="chapter-path-node ${active.has(n.id)?"active":""}"><strong>${i+1}. ${escapeHtml(n.display_label||n.title||n.id||"节点")}</strong><small>${escapeHtml(n.id||"")} · 第 ${escapeHtml(n.chapter_num??"?")} 章 · v${escapeHtml(n.version??"?")}${active.has(n.id)?" · 活跃路径":""}</small>${n.summary?`<p>${escapeHtml(n.summary)}</p>`:""}</article>`).join("")}</div>`; }
function recordValueText(value){ if(value===null||value===undefined||value==="") return "-"; if(typeof value==="object") return JSON.stringify(value,null,2); return String(value); }
function recordCard(label,value,wide=false){ const text=recordValueText(value); const pre=typeof value==="object"&&value!==null; return `<article class="chapter-record-card ${wide?"chapter-record-wide":""}"><span>${escapeHtml(label)}</span>${pre?`<pre class="chapter-record-pre">${escapeHtml(text)}</pre>`:`<p>${escapeHtml(text)}</p>`}</article>`; }
function generationRecordHtml(record={}){ const keys=Object.keys(record||{}); if(!keys.length) return '<div class="notice small">当前节点没有生成记录。</div>'; const cards=[recordCard("章节", `${record.chapter_num||"?"} · ${record.chapter_title||""} · v${record.version||"?"}`), recordCard("模式/操作", [record.generation_mode,record.operation].filter(Boolean).join(" / ")||"classic"), recordCard("模型", record.model), recordCard("创建时间", record.created_at), recordCard("需求", record.requirement||record.polish_requirement||""), recordCard("剧情", record.plot||""), recordCard("监督状态", (record.supervision_report||{}).status||""), recordCard("Agent Run", record.agent_run_id||""), recordCard("内容预览", record.content_preview||"", true), recordCard("监督报告", record.supervision_report, true), recordCard("Agent 数据", record.agent, true), recordCard("世界书维护", record.world_maintenance, true), recordCard("Prompt", record.prompt||"", true)].filter(Boolean); return `<div class="chapter-record-grid">${cards.join("")}</div>`; }
async function showNodePath(){ requireBook(); if(!state.selectedNodeId) throw new Error("请先选择章节树节点"); const data=await api(`/api/books/${enc(state.currentBook)}/nodes/${enc(state.selectedNodeId)}/path`); setChapterInspector("节点路径", chapterPathHtml(data.nodes||[])); }
async function showNodeRecord(){ requireBook(); if(!state.selectedNodeId) throw new Error("请先选择章节树节点"); const data=await api(`/api/books/${enc(state.currentBook)}/nodes/${enc(state.selectedNodeId)}/record`); setChapterInspector("生成记录", generationRecordHtml(data.record||{})); }
async function exportSelectedNode(){ requireBook(); if(!state.selectedNodeId) throw new Error("请先选择章节树节点"); const fmt=$("exportFormat")?$("exportFormat").value:"txt"; const data=await api(`/api/books/${enc(state.currentBook)}/nodes/${enc(state.selectedNodeId)}/export`,{method:"POST",body:JSON.stringify({fmt})}); connectTask(data.task_id, payload=>{ const result=(payload.data||{}).result; const download=(payload.data||{}).download || (result||{}).download; if(download) addDownload(download); }); selectSection("tasks"); }
async function generateNodeVariant(mode){ requireBook(); if(!state.selectedNodeId) throw new Error("请先选择章节树节点"); const body={mode,requirement:$("chapterVariantRequirement")?$("chapterVariantRequirement").value:"",target_words:Number(($("chapterVariantWords")||{}).value||3000)}; const data=await api(`/api/books/${enc(state.currentBook)}/nodes/${enc(state.selectedNodeId)}/variant`,{method:"POST",body:JSON.stringify(body)}); connectTask(data.task_id, payload=>{ if(payload.type==="completed") loadChapters().catch(()=>{}); }); selectSection("tasks"); }
async function rebuildSummary(){ requireBook(); const data=await api(`/api/books/${enc(state.currentBook)}/chapters/rebuild-summary`,{method:"POST",body:"{}"}); connectTask(data.task_id); selectSection("tasks"); }
async function rebuildWorld(){ requireBook(); const data=await api(`/api/books/${enc(state.currentBook)}/world/rebuild`,{method:"POST",body:JSON.stringify({requirement:"extract_missing"})}); connectTask(data.task_id); selectSection("tasks"); }
async function extractNodeWorld(){ requireBook(); if(!state.selectedNodeId) throw new Error("请先选择章节树节点"); const data=await api(`/api/books/${enc(state.currentBook)}/world/extract-node`,{method:"POST",body:JSON.stringify({node_id:state.selectedNodeId})}); connectTask(data.task_id); selectSection("tasks"); }
async function exportBook(){ await startExport(true); }
async function loadWorld(){ if(!state.currentBook) return; const data=await api(`/api/books/${enc(state.currentBook)}/world`); state.world=data.world||{}; $("worldJson").value=JSON.stringify(state.world,null,2); $("worldSummary").textContent=`一致性提醒：${(data.warnings||[]).length} 条`; renderWorldCategories(); renderWorldEntities(); await loadAgentState(false).catch(()=>{}); }
function worldCategoryItems(key=state.worldCategory){
  const value=state.world[key];
  if(Array.isArray(value)) return value.map((item,i)=>({item:item&&typeof item==="object"?item:{value:item},key:String(i),index:i,editable:true}));
  if(value&&typeof value==="object") return Object.entries(value).map(([entryKey,item],i)=>({item:item&&typeof item==="object"?{...item,_key:entryKey}:{value:item,_key:entryKey},key:entryKey,index:i,editable:false}));
  if(value!==undefined&&value!==null&&value!=="") return [{item:{value:value,_key:key},key:key,index:0,editable:false}];
  return [];
}
function worldEntityTitle(item,i){ return item.name||item.title||item.id||item.hint||item.summary||item._key||`条目 ${i+1}`; }
function worldEntitySub(item,entry){ return [item.id||"", item.status||"", item.type||"", entry.editable?"":"只读", item._key||""].filter(Boolean).join(" · "); }
function setWorldEntityEditable(editable){ state.worldEntityEditable=Boolean(editable); const save=$("saveWorldEntityBtn"); const del=$("deleteWorldEntityBtn"); const sync=$("syncWorldEntityFormBtn"); if(save) save.disabled=!editable; if(del) del.disabled=!editable||state.worldIndex<0; if(sync) sync.disabled=!editable; }
function renderWorldCategories(){ renderCards($("worldCategoryList"), worldCategories, ([key,label])=>{ const items=worldCategoryItems(key); const editable=Array.isArray(state.world[key]||[]); const b=buttonCard(label, `${items.length} 项${editable?"":" · 只读"}`, "查看", key===state.worldCategory); b.onclick=()=>{ state.worldCategory=key; state.worldIndex=-1; state.worldEntityEditable=editable; renderWorldCategories(); renderWorldEntities(); };
return b; }); }
function renderWorldEntities(){ const entries=worldCategoryItems(); renderCards($("worldEntityList"), entries, (entry,i)=>{ const item=entry.item||{}; const b=buttonCard(worldEntityTitle(item,i), worldEntitySub(item,entry), entry.editable?"编辑":"查看", i===state.worldIndex); b.onclick=()=>selectWorldEntity(i); return b; }); if(state.worldIndex<0){ $("worldEntityTitle").textContent=Array.isArray(state.world[state.worldCategory])?"世界书详情":"世界书只读视图"; $("worldEntityJson").value=""; renderWorldEntityFields(null); setWorldEntityEditable(Array.isArray(state.world[state.worldCategory]||[])); } }
function worldFieldDisplayValue(value){ if(Array.isArray(value)) return value.join("、"); if(value&&typeof value==="object") return JSON.stringify(value,null,2); if(value===undefined||value===null) return ""; return String(value); }
function renderWorldEntityFields(item){ const box=$("worldEntityFields"); if(!box) return; box.innerHTML=""; if(!item){ box.innerHTML='<div class="notice small">请选择实体，或点击新增后编辑字段。</div>'; return; } const entries=Object.entries(item).filter(([key])=>!key.startsWith("_")).slice(0,24); if(!entries.length){ box.innerHTML='<div class="notice small">该实体暂无可编辑字段。</div>'; return; } for(const [key,value] of entries){ const label=document.createElement("label"); label.dataset.key=key; const textarea=(Array.isArray(value)||value&&typeof value==="object"||String(value||"").length>80); label.innerHTML=`<span>${escapeHtml(key)}</span>${textarea?`<textarea rows="${Array.isArray(value)||value&&typeof value==="object"?4:2}">${escapeHtml(worldFieldDisplayValue(value))}</textarea>`:`<input value="${escapeHtml(worldFieldDisplayValue(value))}">`}`; box.appendChild(label); } }
function parseWorldFieldValue(raw, previous){ const text=String(raw??"").trim(); if(text==="") return Array.isArray(previous)?[]:""; if(Array.isArray(previous)) return text.replace(/[，；]/g,"、").split("、").map(x=>x.trim()).filter(Boolean); if(typeof previous==="boolean") return ["1","true","yes","是","开启"].includes(text.toLowerCase()); if(typeof previous==="number"&&!Number.isNaN(Number(text))) return Number(text); if((text.startsWith("{")&&text.endsWith("}"))||(text.startsWith("[")&&text.endsWith("]"))) try{return JSON.parse(text);}catch(_e){} return text; }
function syncWorldEntityFormToJson(silent=false){ const base=JSON.parse($("worldEntityJson").value||"{}"); document.querySelectorAll("#worldEntityFields label[data-key]").forEach(row=>{ const key=row.dataset.key; const input=row.querySelector("textarea,input"); base[key]=parseWorldFieldValue(input?input.value:"", base[key]); }); $("worldEntityJson").value=JSON.stringify(base,null,2); renderWorldEntityFields(base); if(!silent) toast("表单内容已同步到 JSON"); return base; }
function selectWorldEntity(i){ state.worldIndex=i; const entry=worldCategoryItems()[i]||{item:{},editable:false}; const item=entry.item||{}; $("worldEntityTitle").textContent=worldEntityTitle(item,i); $("worldEntityJson").value=JSON.stringify(item,null,2); renderWorldEntityFields(item); setWorldEntityEditable(entry.editable); renderWorldEntities(); }
async function saveWorldEntity(){ requireBook(); if(!Array.isArray(state.world[state.worldCategory])) throw new Error("当前分类为只读视图，请在完整世界书 JSON 中编辑。"); const data=syncWorldEntityFormToJson(true); const body={category:state.worldCategory,data,index:state.worldIndex>=0?state.worldIndex:null}; const saved=await api(`/api/books/${enc(state.currentBook)}/world/entity`,{method:"POST",body:JSON.stringify(body)}); state.world=saved.world||state.world; $("worldJson").value=JSON.stringify(state.world,null,2); toast("实体已保存"); renderWorldEntityFields(data); renderWorldEntities(); }
async function deleteWorldEntity(){ requireBook(); if(!Array.isArray(state.world[state.worldCategory])) throw new Error("当前分类为只读视图，请在完整世界书 JSON 中编辑。"); if(state.worldIndex<0) throw new Error("请选择实体"); const data=await api(`/api/books/${enc(state.currentBook)}/world/entity`,{method:"DELETE",body:JSON.stringify({category:state.worldCategory,index:state.worldIndex})}); state.world=data.world||state.world; state.worldIndex=-1; $("worldJson").value=JSON.stringify(state.world,null,2); toast("实体已删除"); renderWorldEntities(); }
async function saveWorld(){ requireBook(); await api(`/api/books/${enc(state.currentBook)}/world`,{method:"PUT",body:JSON.stringify({world:JSON.parse($("worldJson").value||"{}")})}); toast("世界书已保存"); await loadWorld(); }
async function auditWorld(){ const data=await api(`/api/books/${enc(state.currentBook)}/world/audit`); $("worldSummary").textContent=`一致性提醒：${(data.warnings||[]).length} 条`; worldOutput(data, "一致性审计"); }
async function analyzeWorld(){ requireBook(); const text=$("worldDetailText").value.trim(); if(!text) throw new Error("请输入补充细节"); const data=await api(`/api/books/${enc(state.currentBook)}/agent/world/analyze`,{method:"POST",body:JSON.stringify({text})}); connectTask(data.task_id); selectSection("tasks"); }
async function loadAgentState(showToast=true){ if(!state.currentBook) return; const data=await api(`/api/books/${enc(state.currentBook)}/agent/state`); state.agentProfiles=data.profiles||[]; state.agentSessions=data.sessions||[]; state.agentAdvice=data.advice||[]; state.advisorHistory=data.advisor_history||[]; state.agentArtifacts=data.artifacts||[]; state.pendingChanges=data.pending_changes||[]; state.pendingWorldMaintenance=data.pending_world_maintenance||[]; const rows=[]; rows.push({title:"Agent Profiles", sub:`${(data.profiles||[]).length} 个`, action:""}); rows.push({title:"待审批变更", sub:`${(data.pending_changes||[]).length} 项`, action:"审批"}); rows.push({title:"构思库", sub:`${state.agentAdvice.length} 条`, action:"预览"}); rows.push({title:"运行产物", sub:`${state.agentArtifacts.length} 个`, action:"查看"}); rows.push({title:"顾问历史", sub:`${state.advisorHistory.length} 条`, action:"管理"}); rows.push({title:"待重试世界书维护", sub:`${state.pendingWorldMaintenance.length} 项`, action:"重试"}); renderCards($("agentStateList"), rows, row=>buttonCard(row.title,row.sub,row.action)); renderPendingChanges(data.pending_changes||[]); renderWorldMaintenance(); renderAgentSessions(); renderAgentProfiles(); renderAgentAdvice(); renderAgentArtifacts(); renderAdvisorHistory(); if(showToast) toast("Agent 状态已刷新"); }
function previewAgentText(title,text){ const box=$("agentInspector")||$("agentPlanView"); if(box) box.value=`【${title}】\n${text||""}`; }
function renderAgentAdvice(){ const box=$("agentAdviceList"); if(!box) return; renderCards(box,state.agentAdvice||[],item=>{ const meta=item.metadata||{}; const title=meta.title||"写作构思"; const b=buttonCard(title,`${item.created_at||""} · ${item.artifact_id||""}`,"预览"); b.onclick=()=>{ state.lastAdvisorResult={run_id:item.run_id||"manual",answer:item.content||""}; previewAgentText(title,item.content||""); };
return b; }); }
function artifactTitle(item){ const meta=item.metadata||{}; return meta.title||item.kind||item.artifact_id||"Agent 产物"; }
function artifactSub(item){ return [item.kind||"", item.created_at||"", item.run_id||""].filter(Boolean).join(" · "); }
function renderAgentArtifacts(){ const box=$("agentArtifactList"); if(!box) return; renderCards(box,state.agentArtifacts||[],item=>{ const b=buttonCard(artifactTitle(item),artifactSub(item),"查看"); b.onclick=()=>loadAgentArtifact(item.artifact_id).catch(e=>toast(e.message)); return b; }); }
async function loadAgentArtifact(artifactId){ requireBook(); if(!artifactId) throw new Error("Agent 产物 ID 为空"); const data=await api(`/api/books/${enc(state.currentBook)}/agent/artifacts/${enc(artifactId)}`); const item=data.artifact||{}; const meta=Object.keys(item.metadata||{}).length?`

【Metadata】
${JSON.stringify(item.metadata,null,2)}`:""; previewAgentText(`运行产物 · ${artifactTitle(item)}`, `${item.content||""}${meta}`); return item; }
function renderAdvisorHistory(){ const box=$("advisorHistoryList"); if(!box) return; renderCards(box,state.advisorHistory||[],item=>{ const idx=Number(item.index); const role=item.role==="user"?"用户":"顾问"; const active=state.selectedAdvisorHistoryIndex===idx; const b=buttonCard(`${idx+1}. ${role}`,`${item.at||""} · ${(item.content||"").replace(/\s+/g," ").slice(0,80)}`,active?"已选":"预览",active); b.onclick=()=>{ state.selectedAdvisorHistoryIndex=idx; previewAgentText(`顾问历史 · ${role}`,item.content||""); renderAdvisorHistory(); };
return b; }); }
async function deleteAdvisorHistory(){ requireBook(); if(state.selectedAdvisorHistoryIndex===null || state.selectedAdvisorHistoryIndex===undefined) throw new Error("请选择一条顾问历史"); await api(`/api/books/${enc(state.currentBook)}/agent/advisor/history/${state.selectedAdvisorHistoryIndex}`,{method:"DELETE"}); state.selectedAdvisorHistoryIndex=null; toast("顾问历史已删除"); await loadAgentState(false); }
async function clearAdvisorHistory(){ requireBook(); if(!confirm("清空本书全部顾问消息？运行记录和构思库不会删除。")) return; const data=await api(`/api/books/${enc(state.currentBook)}/agent/advisor/history`,{method:"DELETE"}); state.selectedAdvisorHistoryIndex=null; toast(`已清空 ${data.removed||0} 条顾问消息`); await loadAgentState(false); }
function changePatchOperations(cs){ const op=(cs.operations||[]).find(item=>item.operation==="world_bible.patch"); return (((op||{}).payload||{}).operations||[]).map(item=>({...item})); }
function renderWorldMaintenance(){ const box=$("agentMaintenanceList"); if(!box) return; renderCards(box,state.pendingWorldMaintenance||[],item=>{ const id=item.task_id||""; const b=buttonCard(id||"维护任务", `第${item.chapter_num||"?"}章 v${item.version||"?"} · ${item.error||"待重试"}`, "重试"); b.onclick=()=>retryWorldMaintenance(id).catch(e=>toast(e.message)); return b; }); }
function renderAgentProfiles(){ const sel=$("workbenchAgentKind"); if(!sel) return; const current=sel.value; sel.innerHTML=""; for(const profile of state.agentProfiles||[]){ const opt=document.createElement("option"); opt.value=profile.agent_kind; opt.textContent=profile.display_name||profile.agent_kind; opt.selected=profile.agent_kind===current; sel.appendChild(opt); } }
function renderAgentSessions(){ const box=$("agentSessionList"); if(!box) return; renderCards(box,state.agentSessions||[],session=>{ const active=session.session_id===state.selectedAgentSessionId; const runId=session.active_run_id || (session.run_ids||[]).slice(-1)[0] || ""; const b=buttonCard(session.title||session.session_id, `${session.agent_kind||""} · ${(session.messages||[]).length} 条消息${runId?" · 有运行":""}`, active?"已选":"选择", active); b.onclick=()=>selectAgentSession(session).catch(e=>toast(e.message)); return b; }); }
async function selectAgentSession(session){ state.selectedAgentSessionId=session.session_id||""; state.activeAgentRunId=session.active_run_id || (session.run_ids||[]).slice(-1)[0] || ""; if($("workbenchAgentKind")) $("workbenchAgentKind").value=session.agent_kind||""; if($("workbenchSessionTitle")) $("workbenchSessionTitle").value=session.title||""; renderAgentSessions(); if(state.activeAgentRunId){ await refreshAgentRun(false); return; } renderAgentRun({status:"completed",terminal_reason:"session_history",messages:session.messages||[],tool_calls:[],change_set_ids:[],artifact_ids:[],usage:{}},[]); }
async function createWorkbenchSession(){ requireBook(); const data=await api(`/api/books/${enc(state.currentBook)}/agent/sessions`,{method:"POST",body:JSON.stringify({agent_kind:$("workbenchAgentKind")?.value||"writing_advisor",title:$("workbenchSessionTitle")?.value||""})}); state.selectedAgentSessionId=(data.session||{}).session_id||""; toast("Agent 会话已创建"); await loadAgentState(false); }
function workbenchReferences(){ return ($("workbenchReferences")?.value||"").split(/\r?\n/).map(x=>x.trim()).filter(Boolean); }
function agentRunEvents(){ if(!state.agentRunEvents) state.agentRunEvents=[]; return state.agentRunEvents; }
function agentEventTitle(type){ return {run_started:"开始运行",usage_updated:"Token 用量",model_stream:"模型输出",tool_call:"工具调用",tool_result:"工具结果",change_set_created:"世界书变更",artifact_saved:"保存产物",run_completed:"运行完成",run_failed:"运行失败"}[type] || type || "事件"; }
function agentRunStatusLabel(status){ return {queued:"排队中",running:"运行中",waiting_approval:"等待确认",paused:"已暂停",completed:"已完成",failed:"失败",cancelled:"已取消"}[status] || status || "未知"; }
function agentExtractText(payload){ if(!payload) return ""; if(typeof payload==="string") return payload; if(Array.isArray(payload)) return payload.map(agentExtractText).filter(Boolean).join(""); const direct=["text","content","message","delta","answer","output","response","result","error"]; for(const key of direct){ const value=payload[key]; if(typeof value==="string" && value) return value; } const nested=[payload.delta,payload.message,payload.output,payload.response,payload.result,payload.data]; for(const value of nested){ const text=agentExtractText(value); if(text) return text; } const choice=(payload.choices||[])[0] || payload.choice; if(choice){ const text=agentExtractText(choice.delta) || agentExtractText(choice.message) || agentExtractText(choice); if(text) return text; } return ""; }
function agentPayloadPreview(payload){ const text=agentExtractText(payload); if(text) return text; if(!payload || typeof payload !== "object") return ""; const keys=Object.keys(payload); if(!keys.length) return ""; return keys.slice(0,4).map(key=>`${key}: ${typeof payload[key] === "object" ? JSON.stringify(payload[key]) : payload[key]}`).join(" · "); }
function agentRunAnswer(run, events=[]){ const messages=(run&&run.messages)||[]; const assistant=[...messages].reverse().find(item=>item.role==="assistant" && agentExtractText(item)); if(assistant) return agentExtractText(assistant); const eventList=events||[]; const completed=[...eventList].reverse().map(ev=>ev.payload||{}).find(payload=>agentExtractText(payload.result)||agentExtractText(payload.answer)||agentExtractText(payload.output)); if(completed){ const text=agentExtractText(completed.result)||agentExtractText(completed.answer)||agentExtractText(completed.output); if(text) return text; } let answer=""; for(const ev of eventList){ if(ev.event_type!=="model_stream"&&ev.event_type!=="assistant_message") continue; const text=agentExtractText(ev.payload); if(!text) continue; if(!answer || text.startsWith(answer)) answer=text; else if(!answer.endsWith(text)) answer+=text; } if(answer) return answer; const artifact=[...eventList].reverse().find(ev=>ev.event_type==="artifact_saved" && agentExtractText(ev.payload)); return artifact?agentExtractText(artifact.payload):""; }
function agentStructuredJson(text){ const raw=String(text||"").trim(); if(!raw) return null; const fenced=raw.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i); const body=(fenced?fenced[1]:raw).trim(); const candidates=[body]; const objStart=body.indexOf("{"); const objEnd=body.lastIndexOf("}"); if(objStart>=0&&objEnd>objStart) candidates.push(body.slice(objStart,objEnd+1)); const arrStart=body.indexOf("["); const arrEnd=body.lastIndexOf("]"); if(arrStart>=0&&arrEnd>arrStart) candidates.push(body.slice(arrStart,arrEnd+1)); for(const candidate of candidates){ try{ const parsed=JSON.parse(candidate); if(parsed&&typeof parsed==="object") return parsed; }catch(_e){} } return null; }
function agentHumanKey(key){ return {answer:"回答",summary:"摘要",title:"标题",plan:"规划",outline:"大纲",steps:"步骤",next_steps:"下一步",suggestions:"建议",risks:"风险",conflicts:"冲突",characters:"角色",locations:"地点",world_bible:"世界书",operations:"变更",chapter_title:"章节标题",chapter_plot:"章节剧情",requirements:"写作要求"}[key]||String(key||"").replace(/_/g," "); }
function agentTextHtml(text){ const parts=String(text||"").trim().split(/\n{2,}/).map(x=>x.trim()).filter(Boolean); if(!parts.length) return ""; return parts.map(part=>{ const lines=part.split(/\n/).map(x=>x.trim()).filter(Boolean); if(lines.length>1&&lines.every(line=>/^([-*]|\d+[.)、])\s+/.test(line))) return `<ul>${lines.map(line=>`<li>${escapeHtml(line.replace(/^([-*]|\d+[.)、])\s+/,""))}</li>`).join("")}</ul>`; return `<p>${escapeHtml(part).replace(/\n/g,"<br>")}</p>`; }).join(""); }
function agentValueHtml(value, depth=0){ if(value===null||value===undefined||value==="") return '<span class="muted">空</span>'; if(typeof value==="string") return agentTextHtml(value); if(typeof value==="number"||typeof value==="boolean") return `<p>${escapeHtml(String(value))}</p>`; if(Array.isArray(value)){ if(!value.length) return '<span class="muted">空列表</span>'; return `<ol class="agent-answer-list">${value.slice(0,24).map(item=>`<li>${typeof item==="object"&&item!==null?agentValueHtml(item,depth+1):agentTextHtml(String(item))}</li>`).join("")}</ol>`; } const entries=Object.entries(value||{}).slice(0,32); if(depth>1) return `<pre class="agent-json-mini">${escapeHtml(JSON.stringify(value,null,2))}</pre>`; return `<dl class="agent-answer-dl">${entries.map(([key,val])=>`<div><dt>${escapeHtml(agentHumanKey(key))}</dt><dd>${agentValueHtml(val,depth+1)}</dd></div>`).join("")}</dl>`; }
function renderAgentStructuredAnswer(value){ if(Array.isArray(value)) return `<div class="agent-answer-grid">${value.map((item,i)=>`<article class="agent-answer-card"><strong>${i+1}</strong>${agentValueHtml(item)}</article>`).join("")}</div>`; const entries=Object.entries(value||{}); if(!entries.length) return '<div class="notice small">Agent 返回了空对象。</div>'; return `<div class="agent-answer-grid">${entries.map(([key,val])=>`<article class="agent-answer-card"><strong>${escapeHtml(agentHumanKey(key))}</strong>${agentValueHtml(val)}</article>`).join("")}</div>`; }
function renderAgentAnswer(answer){ const structured=agentStructuredJson(answer); return `<section class="agent-answer"><h4>${structured?"Agent 结构化结果":"Agent 回答"}</h4>${structured?renderAgentStructuredAnswer(structured):`<div class="agent-answer-text">${agentTextHtml(answer)}</div>`}</section>`; }
function agentModeLabel(value){ return {planning_only:"仅规划",model_completed:"模型完成",approval_required:"等待审批",session_history:"会话历史",agent:"Agent"}[value] || value || "-"; }
function renderAgentRun(run, events){ state.activeAgentRunId=(run||{}).run_id||state.activeAgentRunId; state.agentRun = run || state.agentRun || null; if(events) state.agentRunEvents=[...events]; const box=$("agentRunTimeline"); if(!box) return; const current=state.agentRun||{}; const timeline=agentRunEvents(); const answer=agentRunAnswer(current,timeline); const usage=current.usage||{}; const stats=[[`状态`,agentRunStatusLabel(current.status)], [`模式`,agentModeLabel(current.terminal_reason||current.mode)], [`工具`,(current.tool_calls||[]).length], [`变更`,(current.change_set_ids||[]).length], [`产物`,(current.artifact_ids||[]).length], [`Token`,usage.total_tokens||"-"]]; const eventSection=`<details class="agent-event-drawer"><summary>诊断事件 · ${timeline.length}</summary><section class="agent-event-list">${timeline.length?timeline.map(renderAgentEvent).join(""):`<div class="notice small">暂无运行事件。</div>`}</section></details>`; box.innerHTML=`<div class="agent-run-summary">${stats.map(([k,v])=>`<div class="metric-card"><span>${escapeHtml(k)}</span><b>${escapeHtml(v)}</b></div>`).join("")}</div>${current.run_id?`<div class="agent-run-id">Run ID: ${escapeHtml(current.run_id)}</div>`:""}${answer?renderAgentAnswer(answer):`<div class="notice small">本次运行没有产出可展示的回答，可能只是规划、工具调用或中途停止。可展开诊断事件查看原始详情。</div>`}${renderAgentToolCards(current)}${renderAgentChangeCards(current)}${renderAgentArtifactCards(current)}${eventSection}`; box.querySelectorAll("[data-artifact-id]").forEach(btn=>btn.onclick=()=>loadAgentArtifact(btn.dataset.artifactId).catch(e=>toast(e.message))); }
function agentToolLabel(call){ const req=call.request||{}; return call.tool_name||call.name||req.tool_name||req.name||"工具调用"; }
function agentToolStatus(call){ const result=call.result||{}; if(result.success===true || call.success===true) return "成功"; if(result.success===false || call.success===false) return "失败"; return call.status||"已记录"; }
function renderAgentToolCards(run){ const tools=(run&&run.tool_calls)||[]; if(!tools.length) return ""; return `<section class="agent-chip-section"><h4>工具调用</h4><div class="agent-chip-list">${tools.map((call,i)=>{ const preview=agentPayloadPreview((call.result||{}).structured_data||call.result||call); return `<button type="button" class="agent-chip" title="${escapeHtml(JSON.stringify(call))}"><span>${escapeHtml(i+1)}. ${escapeHtml(agentToolLabel(call))}</span><small>${escapeHtml(agentToolStatus(call))}${preview?` · ${escapeHtml(preview).slice(0,96)}`:""}</small></button>`; }).join("")}</div></section>`; }
function renderAgentChangeCards(run){ const ids=(run&&run.change_set_ids)||[]; if(!ids.length) return ""; return `<section class="agent-chip-section"><h4>世界书变更</h4><div class="agent-chip-list">${ids.map(id=>`<button type="button" class="agent-chip" onclick="loadAgentState(false).catch(e=>toast(e.message))"><span>${escapeHtml(id)}</span><small>到左侧待审批变更确认作用域、批准或拒绝</small></button>`).join("")}</div></section>`; }
function renderAgentArtifactCards(run){ const ids=(run&&run.artifact_ids)||[]; if(!ids.length) return ""; return `<section class="agent-chip-section"><h4>产物</h4><div class="agent-chip-list">${ids.map(id=>`<button type="button" class="agent-chip" data-artifact-id="${escapeHtml(id)}"><span>${escapeHtml(id)}</span><small>点击后在构思与审批预览区查看完整内容</small></button>`).join("")}</div></section>`; }
function renderAgentEvent(ev){ const payload=ev.payload||{}; const preview=agentPayloadPreview(payload); return `<details class="agent-event"><summary><span>${escapeHtml(ev.sequence||"")}. ${escapeHtml(agentEventTitle(ev.event_type))}</span><small>${escapeHtml(ev.timestamp||"")}</small></summary>${preview?`<p>${escapeHtml(preview).replace(/\n/g,"<br>")}</p>`:""}<pre>${escapeHtml(JSON.stringify(payload,null,2))}</pre></details>`; }
function appendAgentTimeline(event){ if(!event) return; state.activeAgentRunId=event.run_id||state.activeAgentRunId; agentRunEvents().push(event); const run=state.agentRun||{run_id:state.activeAgentRunId,status:"running",messages:[],tool_calls:[],change_set_ids:[],artifact_ids:[],usage:{}}; if(event.event_type==="usage_updated") run.usage={...(run.usage||{}),...(event.payload||{})}; renderAgentRun(run, agentRunEvents()); }
async function runWorkbenchAgent(){ requireBook(); if(!state.selectedAgentSessionId) await createWorkbenchSession(); const message=($("workbenchMessage")?.value||"").trim(); if(!message) throw new Error("请输入 Agent 工作台任务描述"); state.agentRunEvents=[]; renderAgentRun({run_id:"",status:"running",terminal_reason:"agent",messages:[],tool_calls:[],change_set_ids:[],artifact_ids:[],usage:{}},[]); const data=await api(`/api/books/${enc(state.currentBook)}/agent/sessions/${enc(state.selectedAgentSessionId)}/run`,{method:"POST",body:JSON.stringify({message,manual_references:workbenchReferences()})}); connectTask(data.task_id,payload=>{ const ev=((payload.data||{}).data||{}).agent_event || (payload.data||{}).agent_event; if(ev) appendAgentTimeline(ev); const result=(payload.data||{}).result; if(result&&result.run) renderAgentRun(result.run,agentRunEvents()); }); selectSection("write"); selectWorkspace("agent"); }
async function refreshAgentRun(ensureVisible=true){ requireBook(); if(!state.activeAgentRunId) throw new Error("暂无 Agent 运行 ID"); const data=await api(`/api/books/${enc(state.currentBook)}/agent/runs/${enc(state.activeAgentRunId)}`); renderAgentRun(data.run,data.events||[]); if(ensureVisible) selectWorkspace("agent"); }
async function controlAgentRun(action,payload={}){ requireBook(); if(!state.activeAgentRunId) throw new Error("暂无活动 Agent 运行"); await api(`/api/books/${enc(state.currentBook)}/agent/runs/${enc(state.activeAgentRunId)}/${action}`,{method:"POST",body:JSON.stringify({payload})}); toast(`Agent 已${action}`); }
async function resumeAgentAfterChange(approved,changeSetId){ if(!state.activeAgentRunId) return; try{ await controlAgentRun("resume",{approved,change_set_id:changeSetId}); }catch(e){ if(!String(e.message||"").includes("活动状态")) throw e; } }
async function retryWorldMaintenance(taskId){ requireBook(); if(!taskId) throw new Error("世界书维护任务 ID 为空"); const data=await api(`/api/books/${enc(state.currentBook)}/agent/world/maintenance/${enc(taskId)}/retry`,{method:"POST",body:"{}"}); connectTask(data.task_id); selectSection("tasks"); }
function renderPendingChangeList(box, changes){ if(!box) return; renderCards(box, changes||[], cs=>{ const id=cs.change_set_id||""; const ops=changePatchOperations(cs); const b=buttonCard(id||"变更", `${cs.status||"pending"} · ${ops.length||(cs.operations||[]).length} 项 · ${cs.reason||""}`, id===state.selectedChangeSetId?"已选":"选择", id===state.selectedChangeSetId); b.onclick=()=>selectPendingChange(cs); return b; }); }
function renderPendingChanges(changes){ renderPendingChangeList($("pendingChangeList"), changes); renderPendingChangeList($("agentPendingChangeList"), changes); }
function selectPendingChange(cs){ state.selectedChangeSetId=cs.change_set_id||""; const ops=changePatchOperations(cs); const lines=[`ChangeSet: ${state.selectedChangeSetId}`, `状态: ${cs.status||""}`, `原因: ${cs.reason||""}`, "", "拟议世界书变更:"]; ops.forEach((op,i)=>lines.push(`${i+1}. ${op.operation} ${op.entity_type}:${op.entity_id}\n   scope=${op.scope||"uncertain"} anchor=${op.anchor_node_id||""}\n   reason=${op.reason||""}\n   payload=${JSON.stringify(op.payload||{})}`)); previewAgentText("世界书变更审批",lines.join("\n")); renderChangeScopeEditor(ops); renderPendingChanges(state.pendingChanges); }
function renderChangeScopeEditor(ops){ const box=$("changeScopeList"); if(!box) return; box.innerHTML=""; if(!ops.length){ box.innerHTML='<div class="notice small">暂无待确认世界书变更</div>'; return; } ops.forEach((op,i)=>{ const row=document.createElement("div"); row.className="change-scope-row"; row.dataset.index=String(i); row.innerHTML=`<strong>${escapeHtml(op.operation||"")} ${escapeHtml(op.entity_type||"")}:${escapeHtml(op.entity_id||"")}</strong><small>${escapeHtml(op.reason||op.scope_reason||"")}</small><div class="inline-form compact"><select data-field="scope"><option value="uncertain">待确认</option><option value="branch">分支后续</option><option value="chapter">仅锚点章节</option><option value="global">全书全局</option></select><input data-field="anchor" placeholder="anchor_node_id；global 留空" value="${escapeHtml(op.anchor_node_id||"")}"></div><small>字段: ${escapeHtml(JSON.stringify(op.payload||{}))}</small>`; row.querySelector('[data-field="scope"]').value=["chapter","branch","global","uncertain"].includes(op.scope)?op.scope:"uncertain"; box.appendChild(row); }); }
function selectedChangeSet(){ return (state.pendingChanges||[]).find(cs=>cs.change_set_id===state.selectedChangeSetId)||null; }
function collectScopeOperations(){ const cs=selectedChangeSet(); if(!cs) throw new Error("请选择一组待审批变更"); const ops=changePatchOperations(cs);
document.querySelectorAll(".change-scope-row").forEach(row=>{ const i=Number(row.dataset.index); if(!ops[i]) return; const scope=row.querySelector('[data-field="scope"]').value; const anchor=row.querySelector('[data-field="anchor"]').value.trim(); if(!["chapter","branch","global"].includes(scope)) throw new Error("所有世界书变更都必须确认作用域"); ops[i].scope=scope; ops[i].anchor_node_id=scope==="global"?"":anchor; ops[i].scope_reason=ops[i].scope_reason||"用户在 Web 审批时确认"; if(scope!=="global"&&!ops[i].anchor_node_id) throw new Error("章节或分支作用域必须填写 anchor_node_id"); }); return ops; }
async function approveSelectedChange(){ requireBook(); const cs=selectedChangeSet(); if(!cs) throw new Error("请选择一组待审批变更"); const operations=collectScopeOperations(); await api(`/api/books/${enc(state.currentBook)}/agent/world/confirm-scopes`,{method:"POST",body:JSON.stringify({change_set_id:cs.change_set_id,operations})}); await api(`/api/books/${enc(state.currentBook)}/agent/changes/approve`,{method:"POST",body:JSON.stringify({change_set_id:cs.change_set_id})}); await resumeAgentAfterChange(true,cs.change_set_id); state.selectedChangeSetId=""; renderChangeScopeEditor([]); toast("世界书变更已批准"); await loadAgentState(false); await loadWorld().catch(()=>{}); }
async function rejectSelectedChange(){ const cs=selectedChangeSet(); if(!cs) throw new Error("请选择一组待审批变更"); await rejectChange(cs.change_set_id); state.selectedChangeSetId=""; renderChangeScopeEditor([]); }
async function approveChange(id){ const cs=(state.pendingChanges||[]).find(item=>item.change_set_id===id); if(cs){ selectPendingChange(cs); await approveSelectedChange(); return; } await api(`/api/books/${enc(state.currentBook)}/agent/changes/approve`,{method:"POST",body:JSON.stringify({change_set_id:id})}); await resumeAgentAfterChange(true,id); toast("变更已批准"); await loadWorld(); }
async function rejectChange(id){ await api(`/api/books/${enc(state.currentBook)}/agent/changes/reject`,{method:"POST",body:JSON.stringify({change_set_id:id})}); await resumeAgentAfterChange(false,id); toast("变更已拒绝"); await loadAgentState(false); }
function selectedWorldEntity(){
  if(state.worldIndex < 0 || !Array.isArray(state.world[state.worldCategory])) return null;
  const list = Array.isArray(state.world[state.worldCategory]) ? state.world[state.worldCategory] : [];
  return list[state.worldIndex] || null;
}
function worldItemTitle(item, index) {
  if (!item || typeof item !== "object") return `条目 ${index + 1}`;
  return item.name || item.title || item.topic || item.hint || item.subject_id || item.id || `条目 ${index + 1}`;
}

function worldItemBody(item) {
  if (!item || typeof item !== "object") return escapeHtml(item || "");
  const fields = [
    ["状态", item.status],
    ["类型", item.type || item.kind || item.predicate],
    ["来源", item.source_chapter || item.chapter],
    ["内容", item.content || item.summary || item.passage || item.full_passage || item.object || item.next_step],
  ].filter(([, value]) => value !== undefined && value !== null && value !== "");
  return fields.map(([label, value]) => `<p><span>${escapeHtml(label)}</span>${escapeHtml(detailText(value))}</p>`).join("");
}

function worldListHtml(title, items = []) {
  const list = Array.isArray(items) ? items : [];
  if (!list.length) return "";
  return `
    <section class="world-output-section">
      <h4>${escapeHtml(title)} <small>${list.length}</small></h4>
      <div class="world-output-grid">
        ${list.map((item, index) => `
          <article class="world-output-card">
            <strong>${escapeHtml(worldItemTitle(item, index))}</strong>
            ${worldItemBody(item)}
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

function worldGroupsHtml(groups = {}) {
  return Object.entries(groups || {})
    .map(([key, items]) => worldListHtml(key, items))
    .filter(Boolean)
    .join("");
}

function worldOutput(data, title = "世界书工具结果") {
  const el = $("worldToolOutput");
  if (!el) return;
  if (typeof data === "string") {
    el.innerHTML = `<section class="world-output-view"><h3>${escapeHtml(title)}</h3><pre class="world-output-text">${escapeHtml(data)}</pre></section>`;
    return;
  }
  const raw = data || {};
  const sections = [];
  if (Array.isArray(raw.warnings)) sections.push(worldListHtml("一致性提醒", raw.warnings.map((text, i) => ({id: `warning-${i + 1}`, content: text}))));
  if (raw.groups) sections.push(worldGroupsHtml(raw.groups));
  if (raw.content) sections.push(`<section class="world-output-section"><h4>注入内容</h4><pre class="world-output-text">${escapeHtml(raw.content)}</pre></section>`);
  if (raw.diagnostics) sections.push(detailPre("检索诊断", raw.diagnostics));
  if (Array.isArray(raw.facts)) sections.push(worldListHtml("事实历史", raw.facts));
  if (Array.isArray(raw.pending)) sections.push(worldListHtml("重复候选", raw.pending));
  if (Array.isArray(raw.merge_history)) sections.push(worldListHtml("合并历史", raw.merge_history));
  if (raw.setting) sections.push(worldListHtml("锁定设定", [raw.setting]));
  if (raw.foreshadowing) sections.push(worldListHtml("新增伏笔", [raw.foreshadowing]));
  if (raw.item) sections.push(worldListHtml("实体状态", [raw.item]));
  const summary = [
    ["完成", raw.ok === undefined ? "-" : (raw.ok ? "是" : "否")],
    ["变更", raw.changed ?? raw.removed ?? raw.merged ?? "-"],
  ];
  el.innerHTML = `
    <section class="world-output-view">
      <h3>${escapeHtml(title)}</h3>
      <div class="task-detail-metrics">${summary.map(([k, v]) => metricHtml(k, v)).join("")}</div>
      ${sections.filter(Boolean).join("") || '<div class="notice small">操作已完成，没有可汇总的结构化条目。</div>'}
      ${detailPre("原始数据", raw)}
    </section>
  `;
}
function splitNames(value){ return String(value||"").replace(/[，、；]/g,",").split(/[,;]+/).map(v=>v.trim()).filter(Boolean); }
async function setWorldEntityField(field, value){ requireBook(); if(state.worldIndex<0) throw new Error("请先选择实体"); const data=await api(`/api/books/${enc(state.currentBook)}/world/entity/state`,{method:"POST",body:JSON.stringify({category:state.worldCategory,index:state.worldIndex,field,value})}); state.world=data.world||state.world; $("worldJson").value=JSON.stringify(state.world,null,2); const item=selectedWorldEntity(); if(item) $("worldEntityJson").value=JSON.stringify(item,null,2); renderWorldEntities(); worldOutput(data.item||data); }
async function toggleWorldHidden(){ const item=selectedWorldEntity(); if(!item) throw new Error("请先选择实体"); await setWorldEntityField("hidden", !Boolean(item.hidden)); toast("显示状态已更新"); }
async function toggleWorldLocked(){ const item=selectedWorldEntity(); if(!item) throw new Error("请先选择实体"); await setWorldEntityField("locked", !Boolean(item.locked)); toast("锁定状态已更新"); }
async function markWorldResolved(){ const item=selectedWorldEntity(); if(item && ["active_plot_threads","global_foreshadowing"].includes(state.worldCategory)){ await setWorldEntityField("status", "resolved"); toast("已标记 resolved"); return; } const query=($("worldQueryInput").value||"").trim(); if(!query) throw new Error("请输入剧情线或伏笔关键词"); const data=await api(`/api/books/${enc(state.currentBook)}/world/resolve`,{method:"POST",body:JSON.stringify({query})}); state.world=data.world||state.world; $("worldJson").value=JSON.stringify(state.world,null,2); renderWorldEntities(); worldOutput(data); toast("已标记解决"); }
async function worldSource(){ requireBook(); const chapter=Number($("worldChapterInput").value||0); const data=await api(`/api/books/${enc(state.currentBook)}/world/source?chapter=${chapter}`); worldOutput(data); }
async function worldPreview(){ requireBook(); const query=($("worldQueryInput").value||$("worldDetailText").value||"").trim(); const data=await api(`/api/books/${enc(state.currentBook)}/world/retrieval-preview`,{method:"POST",body:JSON.stringify({query,token_budget:4000})}); worldOutput(data, "世界书注入预览"); }
async function worldFacts(){ requireBook(); const item=selectedWorldEntity(); const entityId=item ? (item.id||"") : ""; const data=await api(`/api/books/${enc(state.currentBook)}/world/facts?entity_id=${enc(entityId)}`); worldOutput(data); }
async function loadWorldContextPolicies(){ requireBook(); const data=await api(`/api/books/${enc(state.currentBook)}/context-policies`); state.worldPolicyEntities=data.entities||[]; renderWorldPolicies(); worldOutput({entities:state.worldPolicyEntities.length, policies:data.policies||{}}); }
function renderWorldPolicies(){ const box=$("worldPolicyList"); if(!box) return; box.innerHTML=""; if(!state.worldPolicyEntities.length){ box.innerHTML='<div class="notice small">未读取加载策略</div>'; return; } for(const entity of state.worldPolicyEntities){ const p=entity.policy||{}; const row=document.createElement("div"); row.className="world-policy-row"; row.dataset.entityId=entity.entity_id; row.innerHTML=`<label class="check-line"><input data-field="enabled" type="checkbox" ${p.enabled!==false?"checked":""}>启用</label><span><strong>${escapeHtml(entity.name||entity.entity_id)}</strong><small>${escapeHtml(entity.kind||"")} · ${escapeHtml(entity.entity_id||"")}</small></span><select data-field="load_mode"><option value="resident">常驻</option><option value="auto">自动</option><option value="manual">手动</option></select><input data-field="priority" type="number" min="0" max="100" value="${Number(p.priority??50)}"><input data-field="brief_description" placeholder="简介" value="${escapeHtml(p.brief_description||"")}"><input data-field="keywords" placeholder="关键词，逗号分隔" value="${escapeHtml((p.keywords||[]).join("、"))}">`; row.querySelector('[data-field="load_mode"]').value=p.load_mode||"auto"; box.appendChild(row); } }
function collectWorldPolicies(){ const policies={}; document.querySelectorAll(".world-policy-row").forEach(row=>{ const id=row.dataset.entityId; if(!id) return; policies[id]={enabled:row.querySelector('[data-field="enabled"]').checked,load_mode:row.querySelector('[data-field="load_mode"]').value,priority:Number(row.querySelector('[data-field="priority"]').value||50),brief_description:row.querySelector('[data-field="brief_description"]').value,keywords:splitNames(row.querySelector('[data-field="keywords"]').value)}; }); return policies; }
async function saveWorldContextPolicies(){ requireBook(); const policies=collectWorldPolicies(); const data=await api(`/api/books/${enc(state.currentBook)}/context-policies`,{method:"PUT",body:JSON.stringify({policies})}); worldOutput(data); toast("上下文加载策略已保存"); await loadWorldContextPolicies(); }
async function hideLowWorld(){ requireBook(); const data=await api(`/api/books/${enc(state.currentBook)}/world/hide-low-priority`,{method:"POST",body:"{}"}); state.world=data.world||state.world; $("worldJson").value=JSON.stringify(state.world,null,2); renderWorldEntities(); worldOutput(data); toast("低优先级条目已处理"); }
async function lockWorldSetting(){ requireBook(); const topic=($("worldQueryInput").value||"").trim(); if(!topic) throw new Error("请输入设定主题"); const data=await api(`/api/books/${enc(state.currentBook)}/world/lock-setting`,{method:"POST",body:JSON.stringify({topic,passage:$("worldDetailText").value||""})}); state.world=data.world||state.world; $("worldJson").value=JSON.stringify(state.world,null,2); renderWorldCategories(); renderWorldEntities(); worldOutput(data.setting||data); toast("核心设定已锁定"); }
async function addWorldForeshadowing(){ requireBook(); const hint=($("worldQueryInput").value||"").trim(); if(!hint) throw new Error("请输入伏笔内容"); const current=selectedWorldEntity(); const data=await api(`/api/books/${enc(state.currentBook)}/world/foreshadowing`,{method:"POST",body:JSON.stringify({hint,relates_to:(current&&(current.name||current.title||current.id))||"",next_step:$("worldDetailText").value||""})}); state.world=data.world||state.world; $("worldJson").value=JSON.stringify(state.world,null,2); renderWorldCategories(); renderWorldEntities(); worldOutput(data.foreshadowing||data); toast("伏笔已添加"); }
async function mergeWorldCharacters(){ requireBook(); const target=($("worldMergeTarget").value||"").trim(); const mergeNames=splitNames($("worldMergeNames").value); if(!target || !mergeNames.length) throw new Error("请输入保留角色和要合并的角色"); const data=await api(`/api/books/${enc(state.currentBook)}/world/characters/merge`,{method:"POST",body:JSON.stringify({target_name:target,merge_names:mergeNames})}); state.world=data.world||state.world; state.worldCategory="characters"; state.worldIndex=-1; $("worldJson").value=JSON.stringify(state.world,null,2); renderWorldCategories(); renderWorldEntities(); worldOutput(data); toast("角色已合并"); }
async function mergeWorldLocations(){ requireBook(); const target=($("worldMergeTarget").value||"").trim(); const mergeNames=splitNames($("worldMergeNames").value); if(!target || !mergeNames.length) throw new Error("请输入保留地点和要合并的地点"); const data=await api(`/api/books/${enc(state.currentBook)}/world/locations/merge`,{method:"POST",body:JSON.stringify({target_name:target,merge_names:mergeNames})}); state.world=data.world||state.world; state.worldCategory="locations"; state.worldIndex=-1; $("worldJson").value=JSON.stringify(state.world,null,2); renderWorldCategories(); renderWorldEntities(); worldOutput(data); toast("地点已合并"); }
async function reviewWorldDuplicates(){ requireBook(); const data=await api(`/api/books/${enc(state.currentBook)}/world/duplicates`); worldOutput(data); const first=(data.pending||[])[0]; if(first && confirm(`确认合并重复候选：${(first.names||first.entity_ids||[]).join(" / ")}?`)){ const saved=await api(`/api/books/${enc(state.currentBook)}/world/duplicates/confirm`,{method:"POST",body:JSON.stringify({candidate_id:first.id})}); state.world=saved.world||state.world; $("worldJson").value=JSON.stringify(state.world,null,2); renderWorldCategories(); renderWorldEntities(); worldOutput(saved); toast("重复候选已合并"); } }
async function rejectWorldDuplicate(){ requireBook(); const id=prompt("输入要拒绝的候选 ID"); if(!id) return; const data=await api(`/api/books/${enc(state.currentBook)}/world/duplicates/reject`,{method:"POST",body:JSON.stringify({candidate_id:id})}); worldOutput(data); toast("候选已拒绝"); }
async function undoWorldMerge(){ requireBook(); const id=prompt("可选：输入 merge_id；留空撤销最近一次") || ""; const data=await api(`/api/books/${enc(state.currentBook)}/world/merge/undo`,{method:"POST",body:JSON.stringify({merge_id:id})}); state.world=data.world||state.world; $("worldJson").value=JSON.stringify(state.world,null,2); renderWorldCategories(); renderWorldEntities(); worldOutput(data); toast("实体合并已撤销"); }
async function askAdvisor(){ requireBook(); const refs=($("advisorReferences")?.value||"").split(/\r?\n/).map(x=>x.trim()).filter(Boolean); const data=await api(`/api/books/${enc(state.currentBook)}/agent/advisor`,{method:"POST",body:JSON.stringify({message:$("advisorMessage").value,manual_references:refs,fiction_context:$("advisorFictionContext")?.checked!==false})}); connectTask(data.task_id,payload=>{ const result=(payload.data||{}).result; if(result&&result.answer){ state.lastAdvisorResult=result; $("agentPlanView").value="【顾问回答】\n"+result.answer; } }); selectSection("tasks"); }
async function saveAdvisorAdvice(){ requireBook(); const result=state.lastAdvisorResult; const text=(result&&result.answer)||$("agentPlanView").value.replace(/^【顾问回答】\n/,""); if(!text.trim()) throw new Error("没有可保存的顾问回答"); const data=await api(`/api/books/${enc(state.currentBook)}/agent/advice`,{method:"POST",body:JSON.stringify({run_id:(result&&result.run_id)||"manual",text,title:"写作构思"})}); toast(`构思已保存：${data.artifact_id}`); await loadAgentState(false); }
async function advisorAnswerToWorld(){ requireBook(); const result=state.lastAdvisorResult; const text=(result&&result.answer)||$("agentPlanView").value.replace(/^【顾问回答】\n/,""); if(!text.trim()) throw new Error("没有可提交的顾问回答"); const data=await api(`/api/books/${enc(state.currentBook)}/agent/world/analyze`,{method:"POST",body:JSON.stringify({text,source_run_id:(result&&result.run_id)||"manual"})}); connectTask(data.task_id); selectSection("tasks"); }
async function agentPlan(){ requireBook(); const data=await api(`/api/books/${enc(state.currentBook)}/agent/chapter/plan`,{method:"POST",body:JSON.stringify({chapter_title:$("agentChapterTitle").value,plot:$("agentPlot").value,requirement:$("agentRequirement").value,target_words:Number($("agentWords").value||3000),manual_entity_ids:splitNames($("agentManualEntities").value)})}); connectTask(data.task_id, payload=>{ const result=(payload.data||{}).result; const plan=(payload.data||{}).plan || (result||{}).plan; if(plan){ state.lastAgentPlanId=plan.plan_id; $("agentPlanView").value=(payload.data.rendered || (result||{}).rendered || JSON.stringify(plan,null,2)); } }); selectSection("tasks"); }
async function agentGenerate(){ requireBook(); if(!state.lastAgentPlanId) throw new Error("请先生成并确认章节规划"); const data=await api(`/api/books/${enc(state.currentBook)}/agent/chapter/generate`,{method:"POST",body:JSON.stringify({plan_id:state.lastAgentPlanId})}); connectTask(data.task_id); selectSection("tasks"); }
async function polishPlan(){ requireBook(); const node_id=$("polishNodeId").value.trim(); if(!node_id) throw new Error("请输入或选择节点 ID"); const data=await api(`/api/books/${enc(state.currentBook)}/agent/polish/plan`,{method:"POST",body:JSON.stringify({node_id,requirement:$("polishRequirement").value})}); connectTask(data.task_id, payload=>{ const result=(payload.data||{}).result; const plan=(payload.data||{}).plan || (result||{}).plan; if(plan){ state.lastPolishPlanId=plan.plan_id; toast("润色方案已生成"); } }); selectSection("tasks"); }
async function polishGenerate(){ requireBook(); if(!state.lastPolishPlanId) throw new Error("请先规划润色"); const data=await api(`/api/books/${enc(state.currentBook)}/agent/polish/generate`,{method:"POST",body:JSON.stringify({plan_id:state.lastPolishPlanId})}); connectTask(data.task_id); selectSection("tasks"); }
async function extraPlan(){ requireBook(); const body={extra_type:$("extraType").value,start_node_id:$("extraStartNodeId").value,end_node_id:$("extraEndNodeId").value,reference_node_id:$("extraRefNodeId").value,title:$("extraTitle").value,plot:$("extraPlot").value,requirement:$("extraRequirement").value,target_words:Number($("extraWords").value||5000),manual_entity_ids:splitNames($("extraManualEntities").value)}; const data=await api(`/api/books/${enc(state.currentBook)}/agent/extra/plan`,{method:"POST",body:JSON.stringify(body)}); connectTask(data.task_id, payload=>{ const result=(payload.data||{}).result; const plan=(payload.data||{}).plan || (result||{}).plan; if(plan){ state.lastExtraPlanId=plan.plan_id; toast("番外方案已生成"); } }); selectSection("tasks"); }
async function extraGenerate(){ requireBook(); if(!state.lastExtraPlanId) throw new Error("请先规划番外"); const data=await api(`/api/books/${enc(state.currentBook)}/agent/extra/generate`,{method:"POST",body:JSON.stringify({plan_id:state.lastExtraPlanId})}); connectTask(data.task_id); selectSection("tasks"); }
async function createSnapshot(){ requireBook(); const message=($("snapshotMessage")?$("snapshotMessage").value:"").trim(); const query=message?`?message=${enc(message)}`:""; const data=await api(`/api/books/${enc(state.currentBook)}/snapshots${query}`,{method:"POST",body:"{}"}); state.selectedSnapshotId=(data.snapshot||{}).snapshot_id||""; if($("snapshotMessage")) $("snapshotMessage").value=""; toast("快照已创建"); await loadSnapshots(); }
async function loadSnapshots(){ if(!state.currentBook) return; const data=await api(`/api/books/${enc(state.currentBook)}/snapshots`); state.snapshots=data.snapshots||[]; if(state.selectedSnapshotId && !state.snapshots.some(s=>s.snapshot_id===state.selectedSnapshotId)) state.selectedSnapshotId=""; renderCards($("snapshotList"), state.snapshots, snap=>{ const b=buttonCard(snap.message||snap.snapshot_id, `${snap.created_at||""} · ${snap.source||""} · ${(snap.files||[]).length} 文件`, state.selectedSnapshotId===snap.snapshot_id?"已选":"选择", state.selectedSnapshotId===snap.snapshot_id); b.onclick=()=>selectSnapshot(snap); return b; }); if(!state.selectedSnapshotId) renderSnapshotDetail(null); }
function snapshotFileCard(file = {}) {
  const name = file.path || file.file || file.name || "文件";
  const chars = Number(file.chars || file.size || 0);
  const status = file.status || file.change || "已记录";
  return `
    <article class="snapshot-file-card">
      <strong>${escapeHtml(name)}</strong>
      <p><span>状态</span>${escapeHtml(status)}</p>
      <p><span>字符</span>${escapeHtml(chars || "-")}</p>
    </article>
  `;
}

function snapshotChangeCard(change = {}) {
  const path = change.path || change.file || change.name || "文件变化";
  const state = change.status || change.change || change.kind || "变化";
  return `
    <article class="snapshot-file-card">
      <strong>${escapeHtml(path)}</strong>
      <p><span>状态</span>${escapeHtml(state)}</p>
      ${change.reason ? `<p><span>说明</span>${escapeHtml(change.reason)}</p>` : ""}
    </article>
  `;
}

function renderSnapshotDetail(snapshot, status=null){
  const box=$("snapshotDetail");
  if(!box) return;
  if(!snapshot){ box.innerHTML='<div class="notice small">请选择一个快照查看文件变化、恢复或删除。</div>'; return; }
  const files=snapshot.files||[];
  const changes=status?status.changes||[]:null;
  const totalChars=files.reduce((sum,item)=>sum+Number(item.chars||0),0);
  const raw={snapshot_id:snapshot.snapshot_id,message:snapshot.message,source:snapshot.source,created_at:snapshot.created_at,file_count:files.length,total_chars:totalChars,changes};
  box.innerHTML=`
    <section class="snapshot-detail-view">
      <div class="task-detail-metrics">
        ${metricHtml("文件", files.length)}
        ${metricHtml("字符", totalChars)}
        ${metricHtml("来源", snapshot.source || "-")}
        ${metricHtml("变化", changes ? changes.length : "未刷新")}
      </div>
      <div class="tool-box task-detail-main">
        <h3>${escapeHtml(snapshot.message || snapshot.snapshot_id || "快照")}</h3>
        <p class="muted">${escapeHtml(snapshot.snapshot_id || "")}</p>
        <p>${escapeHtml(snapshot.created_at || "")}</p>
      </div>
      ${changes ? `<section class="snapshot-section"><h4>状态变化</h4><div class="snapshot-file-grid">${changes.length ? changes.map(snapshotChangeCard).join("") : '<div class="notice small">当前内容与快照一致。</div>'}</div></section>` : ""}
      <section class="snapshot-section"><h4>快照文件</h4><div class="snapshot-file-grid">${files.length ? files.slice(0,80).map(snapshotFileCard).join("") : '<div class="notice small">该快照没有文件清单。</div>'}</div></section>
      ${detailPre("原始快照", raw)}
    </section>
  `;
}
async function selectSnapshot(snapshot){ state.selectedSnapshotId=snapshot.snapshot_id||""; renderSnapshotDetail(snapshot); renderCards($("snapshotList"), state.snapshots, snap=>{ const b=buttonCard(snap.message||snap.snapshot_id, `${snap.created_at||""} · ${snap.source||""} · ${(snap.files||[]).length} 文件`, state.selectedSnapshotId===snap.snapshot_id?"已选":"选择", state.selectedSnapshotId===snap.snapshot_id); b.onclick=()=>selectSnapshot(snap); return b; }); await showSnapshotStatus(false); }
function selectedSnapshot(){ const snap=state.snapshots.find(s=>s.snapshot_id===state.selectedSnapshotId); if(!snap) throw new Error("请先选择快照"); return snap; }
async function showSnapshotStatus(showToast=true){ requireBook(); const snap=selectedSnapshot(); const data=await api(`/api/books/${enc(state.currentBook)}/snapshots/${enc(snap.snapshot_id)}/status`); renderSnapshotDetail(snap,data); if(showToast) toast("快照状态已刷新"); }
async function restoreSelectedSnapshot(){ requireBook(); const snap=selectedSnapshot(); if(!confirm("恢复该快照？当前状态会先自动备份。")) return; await api(`/api/books/${enc(state.currentBook)}/snapshots/${enc(snap.snapshot_id)}/restore`,{method:"POST",body:"{}"}); toast("快照已恢复"); await loadChapters(); await loadSnapshots(); }
async function deleteSelectedSnapshot(){ requireBook(); const snap=selectedSnapshot(); if(!confirm("删除该快照记录？此操作不会删除当前书籍内容。")) return; await api(`/api/books/${enc(state.currentBook)}/snapshots/${enc(snap.snapshot_id)}`,{method:"DELETE"}); state.selectedSnapshotId=""; toast("快照已删除"); await loadSnapshots(); }
async function startExport(fromBookButton=false){ requireBook(); const fmt=$("exportFormat")?$("exportFormat").value:"txt"; const chapter=$("exportChapterNum")&&$("exportChapterNum").value?Number($("exportChapterNum").value):null; const data=await api(`/api/books/${enc(state.currentBook)}/export`,{method:"POST",body:JSON.stringify({fmt,chapter_num:chapter})}); connectTask(data.task_id, payload=>{ const result=(payload.data||{}).result; const download=(payload.data||{}).download || (result||{}).download; if(download) addDownload(download); }); selectSection("tasks"); }
function addDownload(download){ state.downloads.unshift(download); const token=enc(state.token); for(const id of ["downloadList","tokenDownloadList","taskDownloadList"]){ const el=$(id); if(!el) continue; renderCards(el, state.downloads, d=>{ const a=document.createElement("a"); a.className="item-card"; a.href=`${d.download_url}?token=${token}`; a.innerHTML=`<span><strong>${escapeHtml(d.filename||d.download_id)}</strong><small>${escapeHtml(d.download_id||"")}</small></span><span>下载</span>`; return a; }); } }
function selectContinuationTab(name){
  state.continuationTab=name;
  const ids={source:"contSourcePanel",segments:"contSegmentsPanel",analyze:"contAnalyzePanel",generate:"contGeneratePanel",directions:"contDirectionsPanel",export:"contExportPanel"};
  for(const [key,id] of Object.entries(ids)){ const el=$(id); if(el) el.classList.toggle("hidden", key!==name); }
  document.querySelectorAll(".continuation-tabs button").forEach(b=>b.classList.toggle("active", b.dataset.cont===name));
  if(name==="export") loadContinuationRuns().catch(e=>toast(e.message));
}
function continuationTitle(){ return ($("contAnalysisTitle")?.value||$("contBookSelect")?.value||state.currentBook||$("newBookTitle")?.value||"续写作品").trim(); }
function sourceFromSections(){ return (state.sections||[]).map(s=>`${s.title||"分段"}\n${s.content||""}`).join("\n\n"); }
function setContinuationSections(sections){ state.sections=(sections||[]).filter(s=>(s.content||"").trim()); state.selectedSectionIndex=state.sections.length?0:-1; renderSections(); if(state.selectedSectionIndex>=0) selectContinuationSection(state.selectedSectionIndex); }
function renderContinuationFiles(){ renderCards($("contFileList"), state.contFiles||[], file=>{ const b=buttonCard(file.filename, `${file.chars||0} 字${file.needs_ai?" · 建议 AI 分段":""}`, "载入"); b.onclick=()=>{ $("contSource").value=file.content||""; if(file.sections) setContinuationSections(file.sections); selectContinuationTab("segments"); };
return b; }); }
async function uploadContinuationFiles(){ const input=$("contFiles"); if(!input.files || !input.files.length) throw new Error("请选择一个或多个源文件"); const form=new FormData(); Array.from(input.files).forEach(file=>form.append("files", file, file.name)); const data=await api("/api/continuation/uploads",{method:"POST",body:form}); state.contFiles=data.files||[]; renderContinuationFiles(); const merged=state.contFiles.map(f=>f.content||"").join("\n\n"); if(merged) $("contSource").value=merged; const sections=[]; state.contFiles.forEach(file=>(file.sections||[]).forEach(sec=>sections.push(sec))); if(sections.length) setContinuationSections(sections); toast(`已读取 ${state.contFiles.length} 个文件`); }
async function segmentText(){ const data=await api("/api/continuation/segment",{method:"POST",body:JSON.stringify({text:$("contSource").value})}); setContinuationSections(data.sections||[]); selectContinuationTab("segments"); }
async function agentSegmentText(){ const data=await api("/api/continuation/segment-agent",{method:"POST",body:JSON.stringify({text:$("contSource").value,title:continuationTitle(),use_agent:true})}); setContinuationSections(data.sections||[]); selectContinuationTab("segments"); if(data.fallback) toast(data.error||"已使用本地分段兜底"); }
function renderSections(){ renderCards($("sectionList"), state.sections||[], (sec,i)=>{ const b=buttonCard(`${i+1}. ${sec.title||"未命名"}`, `${(sec.content||"").length} 字`, i===state.selectedSectionIndex?"当前":"预览", i===state.selectedSectionIndex); b.onclick=()=>selectContinuationSection(i); return b; }); }
function selectContinuationSection(i){ state.selectedSectionIndex=i; const sec=(state.sections||[])[i]||{}; $("sectionPreviewTitle").textContent=sec.title||"分段预览"; $("sectionTitleEdit").value=sec.title||""; $("sectionContentEdit").value=sec.content||""; renderSections(); }
function saveCurrentSection(){ const i=state.selectedSectionIndex; if(i<0) throw new Error("请选择分段"); state.sections[i]={title:$("sectionTitleEdit").value||`分段 ${i+1}`,content:$("sectionContentEdit").value}; renderSections(); toast("分段已保存"); }
function deleteCurrentSection(){ const i=state.selectedSectionIndex; if(i<0) throw new Error("请选择分段"); state.sections.splice(i,1); state.selectedSectionIndex=Math.min(i,state.sections.length-1); renderSections(); if(state.selectedSectionIndex>=0) selectContinuationSection(state.selectedSectionIndex); else { $("sectionTitleEdit").value=""; $("sectionContentEdit").value=""; } }
function mergeNextSection(){ const i=state.selectedSectionIndex; if(i<0 || i>=state.sections.length-1) throw new Error("没有下一段可合并"); state.sections[i].content=`${state.sections[i].content}\n\n${state.sections[i+1].content}`; state.sections[i].title=`${state.sections[i].title} + ${state.sections[i+1].title}`; state.sections.splice(i+1,1); selectContinuationSection(i); }
function splitCurrentSection(){ const i=state.selectedSectionIndex; if(i<0) throw new Error("请选择分段"); const editor=$("sectionContentEdit"); const pos=editor.selectionStart||0; const text=editor.value; if(pos<=0 || pos>=text.length) throw new Error("请把光标放在要拆分的位置"); state.sections[i]={title:$("sectionTitleEdit").value||`分段 ${i+1}`,content:text.slice(0,pos).trim()}; state.sections.splice(i+1,0,{title:`${state.sections[i].title} 下`,content:text.slice(pos).trim()}); selectContinuationSection(i+1); }
async function importSections(){ const title=continuationTitle(); const data=await api("/api/continuation/import",{method:"POST",body:JSON.stringify({title,sections:state.sections})}); connectTask(data.task_id); selectSection("tasks"); }
function continuationSettingsCards(settings = {}) {
  const fields = [
    ["主角", settings.protagonist_bio],
    ["背景", settings.background_story],
    ["要求", settings.writing_demand],
    ["规划", settings.author_plan],
    ["题材", settings.genre],
    ["风格", settings.style_tone],
  ].filter(([, value]) => value);
  if (!fields.length) return "";
  return `<section class="continuation-result-section"><h4>小说设定</h4><div class="continuation-result-grid">${fields.map(([label, value]) => `<article class="continuation-result-card"><span>${escapeHtml(label)}</span><p>${escapeHtml(detailText(value))}</p></article>`).join("")}</div></section>`;
}

function continuationListSection(title, items = []) {
  const list = Array.isArray(items) ? items : [];
  if (!list.length) return "";
  return `<section class="continuation-result-section"><h4>${escapeHtml(title)} <small>${list.length}</small></h4><div class="continuation-result-grid">${list.map((item, index) => `<article class="continuation-result-card"><strong>${escapeHtml(item.title || item.name || item.chapter_title || `条目 ${index + 1}`)}</strong><p>${escapeHtml(typeof item === "string" ? item : (item.summary || item.content || item.description || detailText(item)))}</p></article>`).join("")}</div></section>`;
}

function renderContinuationAnalysis(result = {}, title = "续写分析结果") {
  const box = $("analysisResult");
  if (!box) return;
  if (!result || !Object.keys(result).length) {
    box.innerHTML = '<div class="notice small">暂无分析结果。</div>';
    return;
  }
  const settings = result.settings || result.meta || {};
  const summaries = result.summaries || result.chapter_summaries || [];
  const directions = result.directions || [];
  const world = result.world || result.world_bible || {};
  const worldCounts = world && typeof world === "object" ? Object.entries(world).filter(([, value]) => Array.isArray(value)).map(([key, value]) => `${key}: ${value.length}`).join(" · ") : "-";
  box.innerHTML = `
    <section class="continuation-result-view">
      <div class="task-detail-metrics">
        ${metricHtml("书名", result.title || state.currentBook || "-")}
        ${metricHtml("章节", result.chapter_num || result.imported_chapters || summaries.length || "-")}
        ${metricHtml("方向", directions.length || "-")}
        ${metricHtml("世界书", worldCounts || "-")}
      </div>
      ${continuationSettingsCards(settings)}
      ${continuationListSection("章节摘要", summaries)}
      ${continuationListSection("发展方向", directions)}
      ${detailPre("世界书摘要", world)}
      ${detailPre("原始分析", result)}
    </section>
  `;
}

function renderContinuationRunDetail(run = {}) {
  const box = $("continuationRunDetail");
  if (!box) return;
  if (!run || !Object.keys(run).length) {
    box.innerHTML = '<div class="notice small">请选择一次续写运行查看详情。</div>';
    return;
  }
  const result = run.result || {};
  const summary = run.output_summary || {};
  const directions = result.directions || [];
  box.innerHTML = `
    <section class="continuation-result-view">
      <div class="task-detail-metrics">
        ${metricHtml("任务", run.task || "-")}
        ${metricHtml("状态", run.status || "-")}
        ${metricHtml("书名", run.book_title || result.title || "-")}
        ${metricHtml("章节", summary.chapter_num || result.chapter_num || "-")}
      </div>
      <div class="tool-box task-detail-main">
        <h3>${escapeHtml(run.task || run.run_id || "续写运行")}</h3>
        <p class="muted">${escapeHtml(run.run_id || "")}</p>
        <p>${escapeHtml(run.created_at || "")}</p>
      </div>
      ${continuationSettingsCards(result.settings || result.meta || {})}
      ${continuationListSection("发展方向", directions)}
      ${detailPre("输出摘要", summary)}
      ${detailPre("原始运行", run)}
    </section>
  `;
}
async function analyzeContinuation(){ const title=continuationTitle(); if($("contAnalysisTitle")) $("contAnalysisTitle").value=title; const xpMode=$("contAnalyzeXpMode")?.checked||$("contMetaXpMode")?.checked||false; const data=await api("/api/continuation/analyze",{method:"POST",body:JSON.stringify({title,sections:state.sections,source_text:$("contSource").value,xp_mode:xpMode})}); connectTask(data.task_id, payload=>{ const result=(payload.data||{}).result; if(result){ setCurrentBook(result.title||title); renderContinuationAnalysis(result,"续写分析结果"); } }); selectSection("tasks"); }
async function suggestContinuation(){ const title=continuationTitle(); const xpMode=$("contXpMode")?.checked||$("contMetaXpMode")?.checked||false; const data=await api("/api/continuation/suggest",{method:"POST",body:JSON.stringify({title,setting:$("contSetting")?.value||"",plot:$("contPlot")?.value||"",xp_mode:xpMode})}); connectTask(data.task_id, payload=>{ const result=(payload.data||{}).result; if(result&&result.directions) renderDirections(result.directions); }); selectSection("tasks"); }
function renderDirections(directions){ renderCards($("directionList"), directions||[], (text,i)=>{ const b=buttonCard(`方向 ${i+1}`, text, "填入"); b.onclick=()=>{ $("contPlot").value=text; selectContinuationTab("generate"); };
return b; }); }
function applyManualDirection(){ $("contPlot").value=$("manualDirectionText").value; selectContinuationTab("generate"); }
async function generateContinuation(){ const title=continuationTitle(); if(state.currentBook && title===state.currentBook && $("contMetaProtagonist")) await saveContinuationMeta(false); const xpMode=$("contXpMode")?.checked||$("contMetaXpMode")?.checked||false; const body={title,source_text:$("contSource").value||sourceFromSections(),chapter_title:$("contChapterTitle").value,requirement:$("contRequirement").value,plot:$("contPlot").value,setting:$("contSetting").value,target_words:Number($("contWords").value||3000),xp_mode:xpMode,chapter_mode:$("contChapterMode")?.checked!==false}; const data=await api("/api/continuation/generate",{method:"POST",body:JSON.stringify(body)}); connectTask(data.task_id, payload=>{ if(payload.type==="completed") updateContinuationChapterInfo().catch(()=>{}); }); selectSection("tasks"); }
async function quickAnalyzeContinuation(){ selectContinuationTab("analyze"); await analyzeContinuation(); }
async function quickGenerateContinuation(){ selectContinuationTab("generate"); await generateContinuation(); }
async function quickSuggestContinuation(){ selectContinuationTab("directions"); await suggestContinuation(); }
async function loadContinuationRuns(){
  const title=state.currentBook?`?title=${enc(state.currentBook)}`:"";
  const data=await api(`/api/continuation/runs${title}`);
  renderCards($("continuationRuns"), data.runs||[], run=>{
    const active=state.selectedContinuationRun&&state.selectedContinuationRun.run_id===run.run_id;
    const summary=run.output_summary||{};
    const bits=[run.book_title||"", run.created_at||"", summary.chapter_num?`第${summary.chapter_num}章`:"", summary.directions?`${summary.directions} 个方向`:""].filter(Boolean);
    const b=buttonCard(run.task||run.run_id||"续写运行", bits.join(" · "), active?"已选":(run.status||"详情"), active);
    b.onclick=()=>showContinuationRun(run.run_id, run.book_title).catch(e=>toast(e.message));
    return b;
  });
}
async function showContinuationRun(runId, title=""){
  if(!runId) throw new Error("续写运行 ID 为空");
  const query=title?`?title=${enc(title)}`:"";
  const data=await api(`/api/continuation/runs/${enc(runId)}${query}`);
  state.selectedContinuationRun=data.run||null;
  renderContinuationRunDetail(state.selectedContinuationRun||{});
  const result=(state.selectedContinuationRun||{}).result||{};
  if(result.settings||result.meta) renderContinuationAnalysis(result,"历史分析结果");
  if(result.directions) renderDirections(result.directions);
  await loadContinuationRuns();
}
function continuationRunResult(){ return (state.selectedContinuationRun||{}).result||{}; }
function applyContinuationRunSettings(){
  const result=continuationRunResult();
  const settings=result.settings||result.meta||{};
  if(!Object.keys(settings).length) throw new Error("选中的续写运行没有可回填的设定");
  applyContinuationMeta(settings);
  const settingText=[settings.background_story,settings.protagonist_bio,settings.writing_demand,settings.author_plan].filter(Boolean).join("\n\n");
  if(settingText) setValueIfPresent("contSetting", settingText);
  if(result.title) setValueIfPresent("contAnalysisTitle", result.title);
  renderContinuationAnalysis(result,"已回填的续写设定");
  selectContinuationTab("generate");
  toast("续写设定已从历史回填");
}
function applyContinuationRunDirections(){
  const run=state.selectedContinuationRun||{};
  const result=run.result||{};
  const directions=result.directions||[];
  if(directions.length){
    renderDirections(directions);
    setValueIfPresent("contPlot", directions[0]);
    selectContinuationTab("directions");
    toast("发展方向已从历史回填");
    return;
  }
  const input=run.input_summary||{};
  if(input.plot||input.requirement||input.setting){
    setValueIfPresent("contPlot", input.plot||"");
    setValueIfPresent("contRequirement", input.requirement||"");
    setValueIfPresent("contSetting", input.setting||"");
    selectContinuationTab("generate");
    toast("续写参数已从历史回填");
    return;
  }
  throw new Error("选中的续写运行没有可回填的发展方向");
}
async function continuationExport(){ const title=continuationTitle(); const fmt=$("contExportFormat").value; const chapter=$("contExportChapterNum").value?Number($("contExportChapterNum").value):null; const data=await api(`/api/books/${enc(title)}/export`,{method:"POST",body:JSON.stringify({fmt,chapter_num:chapter})}); connectTask(data.task_id, payload=>{ const result=(payload.data||{}).result; const download=(payload.data||{}).download || (result||{}).download; if(download) addDownload(download); }); selectSection("tasks"); }
async function loadNoteTree(){ const data=await api("/api/markdown/tree"); renderCards($("noteTree"), data.items||[], item=>{ const active=item.path===state.selectedNotePath; const b=buttonCard(item.name,item.path,item.type==="folder"?"文件夹":"打开",active); b.onclick=()=>selectNoteItem(item); return b; }); }
function selectNoteItem(item){ state.selectedNotePath=item.path; state.selectedNoteType=item.type; $("notePath").value=item.path; $("noteTitle").textContent=item.name||"Markdown"; if(item.type==="folder"){ $("noteFolderPath").value=item.path; $("notePreview").innerHTML='<div class="notice small">已选择文件夹，可重命名、删除或导出。</div>'; renderCards($("noteTree"), [], ()=>document.createElement("div")); loadNoteTree().catch(()=>{}); return; } openNote(item.path).catch(e=>toast(e.message)); }
async function openNote(path){ if(!path) throw new Error("请输入笔记路径"); state.selectedNotePath=path; state.selectedNoteType="file"; $("notePath").value=path; const data=await api(`/api/markdown/file?path=${enc(path)}`); $("noteContent").value=data.content||""; $("noteTitle").textContent=path; await previewNote(false).catch(()=>{}); await loadNoteTree(); }
async function saveNote(show=true){ const path=$("notePath").value.trim(); if(!path) throw new Error("请输入笔记路径"); const data=await api("/api/markdown/file",{method:"PUT",body:JSON.stringify({path,content:$("noteContent").value})}); state.selectedNotePath=data.path||path; state.selectedNoteType="file"; $("notePath").value=state.selectedNotePath; if(show) toast("笔记已保存"); await loadNoteTree(); }
async function previewNote(saveFirst=true){ const path=$("notePath").value.trim(); if(!path) throw new Error("请输入笔记路径"); if(saveFirst) await saveNote(false); const data=await api(`/api/markdown/preview?path=${enc(path)}`); $("notePreview").innerHTML=data.html||""; }
async function createNoteFolder(){ const path=$("noteFolderPath").value.trim(); if(!path) throw new Error("请输入文件夹路径"); await api("/api/markdown/folder",{method:"POST",body:JSON.stringify({path})}); toast("文件夹已创建"); await loadNoteTree(); }
async function renameNote(){ const path=state.selectedNotePath||$("notePath").value.trim(); if(!path) throw new Error("请选择文件或文件夹"); const next=prompt("新路径", path); if(!next||next===path) return; const data=await api("/api/markdown/rename",{method:"POST",body:JSON.stringify({path,new_path:next})}); state.selectedNotePath=data.path||next; $("notePath").value=state.selectedNotePath; toast("已重命名"); await loadNoteTree(); if(state.selectedNoteType==="file") await openNote(state.selectedNotePath); }
async function deleteNote(){ const path=state.selectedNotePath||$("notePath").value.trim(); if(!path) throw new Error("请选择文件或文件夹"); if(!confirm(`删除 ${path}？`)) return; await api(`/api/markdown/path?path=${enc(path)}`,{method:"DELETE"}); state.selectedNotePath=""; $("notePath").value=""; $("noteContent").value=""; $("notePreview").innerHTML=""; toast("已删除"); await loadNoteTree(); }
async function exportNote(){ const path=state.selectedNotePath||$("notePath").value.trim(); const folder=state.selectedNoteType==="folder" || (!path && $("noteFolderPath").value.trim()); const body={path: folder?(path||$("noteFolderPath").value.trim()):path, folder}; const data=await api("/api/markdown/export",{method:"POST",body:JSON.stringify(body)}); addDownload(data.download); toast("笔记导出已生成"); }
function parseIdList(value){ return String(value||"").split(/[,，\s]+/).map(v=>v.trim()).filter(Boolean); }
function fillSelect(el, items, idKey, labelKey, emptyLabel){ el.innerHTML=`<option value="">${emptyLabel}</option>`; for(const item of items||[]){ const opt=document.createElement("option"); opt.value=item[idKey]||""; opt.textContent=item[labelKey]||item[idKey]||"未命名"; el.appendChild(opt); } }
function roleplayStatusLabel(status) {
  return {pending:"待处理", applied:"已应用", rejected:"已拒绝", reverted:"已撤销"}[status] || status || "未标记";
}

function roleplayProfileName(characterId) {
  const profile = (state.roleBook.profiles || []).find(item => item.character_id === characterId);
  return profile ? (profile.name || profile.character_id || characterId) : (characterId || "未指定角色");
}

function memoryChangeCard(change = {}) {
  const fields = [
    ["角色", roleplayProfileName(change.character_id)],
    ["字段", change.field_name || change.field || "-"],
    ["新值", change.new_value],
    ["原因", change.reason],
    ["风险", change.risk],
  ].filter(([, value]) => value !== undefined && value !== null && value !== "");
  return `
    <article class="memory-change-card">
      <strong>${escapeHtml(change.change_id || change.field_name || "记忆变更")}</strong>
      ${fields.map(([label, value]) => `<p><span>${escapeHtml(label)}</span>${escapeHtml(detailText(value))}</p>`).join("")}
    </article>
  `;
}

function renderRoleplayMemoryDetail(changeSet = null, title = "人物书 / 记忆变更") {
  const box = $("roleplayMemoryDetail");
  if (!box) return;
  if (!changeSet) {
    box.innerHTML = '<div class="notice small">选择记忆变更、消息来源或时间线后在这里查看结构化结果。</div>';
    return;
  }
  const changes = Array.isArray(changeSet.changes) ? changeSet.changes : [];
  const sourceIds = (changeSet.source_message_ids || []).join("、");
  box.innerHTML = `
    <section class="memory-detail-view">
      <div class="memory-detail-head">
        <h4>${escapeHtml(title)}</h4>
        <span>${escapeHtml(roleplayStatusLabel(changeSet.status))}</span>
      </div>
      <div class="task-detail-metrics">
        ${metricHtml("变更 ID", changeSet.change_set_id || "-")}
        ${metricHtml("分支", changeSet.branch_id || "-")}
        ${metricHtml("来源消息", sourceIds || "-")}
        ${metricHtml("条目数", changes.length)}
      </div>
      ${changes.length ? `<div class="memory-change-grid">${changes.map(memoryChangeCard).join("")}</div>` : '<div class="notice small">没有结构化变更条目。</div>'}
      ${changeSet.summary ? `<p>${escapeHtml(changeSet.summary)}</p>` : ""}
      ${detailPre("原始变更", changeSet)}
    </section>
  `;
}

function renderRoleplayDataDetail(title, data = {}) {
  const box = $("roleplayMemoryDetail");
  if (!box) return;
  const message = data.message || {};
  const source = data.source || {};
  const timeline = Array.isArray(data.timeline) ? data.timeline : (Array.isArray(data) ? data : []);
  const changes = data.memory_change_sets || [];
  box.innerHTML = `
    <section class="memory-detail-view">
      <div class="memory-detail-head"><h4>${escapeHtml(title)}</h4><span>${escapeHtml(message.speaker_name || source.branch_id || "")}</span></div>
      ${message.content ? `<article class="memory-change-card"><strong>消息内容</strong><p>${escapeHtml(message.content)}</p></article>` : ""}
      ${Object.keys(source).length ? detailPre("来源信息", source) : ""}
      ${timeline.length ? `<div class="memory-change-grid">${timeline.map((item, index) => `<article class="memory-change-card"><strong>${escapeHtml(item.summary || item.event_id || `事件 ${index + 1}`)}</strong>${item.at || item.chapter ? `<p><span>位置</span>${escapeHtml(item.at || item.chapter)}</p>` : ""}${item.details ? `<p>${escapeHtml(item.details)}</p>` : ""}</article>`).join("")}</div>` : ""}
      ${changes.length ? `<div class="memory-change-grid">${changes.map(change => renderRoleplayMemoryDetailInline(change)).join("")}</div>` : ""}
      ${detailPre("原始数据", data)}
    </section>
  `;
}

function renderRoleplayMemoryDetailInline(changeSet = {}) {
  const changes = Array.isArray(changeSet.changes) ? changeSet.changes : [];
  return `
    <article class="memory-change-card">
      <strong>${escapeHtml(changeSet.change_set_id || "记忆变更")}</strong>
      <p><span>状态</span>${escapeHtml(roleplayStatusLabel(changeSet.status))}</p>
      <p><span>条目</span>${changes.length}</p>
    </article>
  `;
}

function selectedMemoryChange() {
  return (state.memoryChangeSets || []).find(item => (item.change_set_id || "") === state.selectedMemoryChangeId) || null;
}
function renderMemoryChanges(){ const box=$("memoryChangeList"); if(!box) return; renderCards(box,state.memoryChangeSets||[],cs=>{ const active=state.selectedMemoryChangeId===(cs.change_set_id||""); const b=buttonCard(cs.change_set_id||"记忆变更",`${roleplayStatusLabel(cs.status)} · ${(cs.changes||[]).length} 项`,active?"已选":"选择",active); b.onclick=()=>{ state.selectedMemoryChangeId=cs.change_set_id||""; setValueIfPresent("characterBookJson", JSON.stringify(cs,null,2)); renderRoleplayMemoryDetail(cs,"待审批记忆变更"); renderMemoryChanges(); };
return b; }); if(!state.memoryChangeSets.length) renderRoleplayMemoryDetail(null); }
async function loadCharacterBook(){ const data=await api("/api/roleplay/character-book"); state.roleBook=data.book||state.roleBook; setValueIfPresent("characterBookJson", JSON.stringify(state.roleBook,null,2)); renderRoleList(); toast("人物书已读取"); }
async function saveCharacterBook(){ const text=$("characterBookJson")?.value||""; const book=JSON.parse(text||"{}"); const data=await api("/api/roleplay/character-book",{method:"PUT",body:JSON.stringify({book})}); state.roleBook=data.book||{}; setValueIfPresent("characterBookJson", JSON.stringify(state.roleBook,null,2)); renderRoleList(); toast("人物书已保存"); }
async function loadChatMemory(){ if(!state.currentConversationId) throw new Error("请先打开或保存会话"); const data=await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/memory`); state.memoryChangeSets=data.memory_change_sets||[]; state.roleBook=data.book||state.roleBook; renderMemoryChanges(); renderRoleBookDetail(selectedRoleProfile()); setValueIfPresent("characterBookJson", JSON.stringify({timeline:data.timeline||[],memory_change_sets:state.memoryChangeSets,character_book_snapshot:data.character_book_snapshot||{}},null,2)); renderRoleplayDataDetail("会话记忆状态", data); return data; }
async function showChatTimeline(){ const data=await loadChatMemory(); setValueIfPresent("characterBookJson", JSON.stringify(data.timeline||[],null,2)); renderRoleplayDataDetail("会话时间线", data.timeline||[]); toast("时间线已读取"); }
async function updateMemoryChange(action){ if(!state.currentConversationId) throw new Error("请先打开或保存会话"); if(!state.selectedMemoryChangeId) throw new Error("请选择记忆变更"); const data=await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/memory/${enc(state.selectedMemoryChangeId)}/${action}`,{method:"POST",body:"{}"}); state.memoryChangeSets=data.memory_change_sets||[]; state.roleBook=data.book||state.roleBook; const selected=selectedMemoryChange(); renderRoleList(); renderMemoryChanges(); if(selected) setValueIfPresent("characterBookJson", JSON.stringify(selected,null,2)); renderRoleplayMemoryDetail(selected,{apply:"记忆变更已应用",reject:"记忆变更已拒绝",revert:"记忆变更已撤销"}[action]||"记忆变更结果"); toast(action==="apply"?"记忆变更已应用":action==="reject"?"记忆变更已拒绝":"记忆变更已撤销"); }
async function editMemoryChange(){ if(!state.currentConversationId) throw new Error("请先打开或保存会话"); if(!state.selectedMemoryChangeId) throw new Error("请选择记忆变更"); let data={}; try{ data=JSON.parse($("characterBookJson")?.value||"{}"); }catch(e){ throw new Error("记忆变更 JSON 无效："+e.message); } const changes=Array.isArray(data)?data:(data.changes||[]); if(!Array.isArray(changes) || !changes.length) throw new Error("JSON 中缺少 changes 数组"); const result=await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/memory/${enc(state.selectedMemoryChangeId)}`,{method:"PUT",body:JSON.stringify({changes})}); state.memoryChangeSets=result.memory_change_sets||[]; const selected=selectedMemoryChange(); renderMemoryChanges(); if(selected) setValueIfPresent("characterBookJson", JSON.stringify(selected,null,2)); renderRoleplayMemoryDetail(selected,"记忆变更修改已保存"); toast("记忆变更修改已保存"); }
function renderRoleControls(){ if($("senderProfileSelect")) fillSelect($("senderProfileSelect"), state.senderProfiles, "sender_profile_id", "name", "选择发送者档案"); if($("scenePresetSelect")) fillSelect($("scenePresetSelect"), state.scenePresets, "scene_preset_id", "name", "选择场景预设"); }
async function loadRoles(){
  const [roles,convs,senders,scenes]=await Promise.all([api("/api/roleplay/characters"),api("/api/roleplay/conversations"),api("/api/roleplay/senders"),api("/api/roleplay/scenes")]);
  state.roleBook=(roles.book||{}); state.senderProfiles=senders.profiles||[]; state.scenePresets=scenes.presets||[]; renderRoleList(); renderConversationList(convs.conversations||[]); renderRoleControls();
}
function renderRoleList(){ const profiles=(state.roleBook.profiles||[]); renderCards($("roleList"), profiles, p=>{ const active=state.selectedRoleIds.includes(p.character_id); const sub=[p.identity||"", p.status||"", (p.aliases||[]).join("、")].filter(Boolean).join(" · ") || p.personality || ""; const b=buttonCard(p.name||p.character_id, sub, active?"已选":"选择", active); b.onclick=()=>{ if(active) state.selectedRoleIds=state.selectedRoleIds.filter(id=>id!==p.character_id); else state.selectedRoleIds.push(p.character_id); fillRoleEditor(active?selectedRoleProfile():p); renderRoleList(); };
return b; }); renderRoleBookDetail(selectedRoleProfile()); }
function selectedRoleProfile(){ const profiles=(state.roleBook.profiles||[]); return profiles.find(p=>p.character_id===(state.selectedRoleIds[0]||""))||null; }
function selectedRoleMemory(profile){ if(!profile) return null; return (state.roleBook.memories||[]).find(m=>m.character_id===profile.character_id)||null; }
function splitRoleAliases(value){ return String(value||"").replace(/[，；]/g,"、").split(/[、,;\r\n]+/).map(v=>v.trim()).filter(Boolean); }
function fillRoleEditor(p){ if(!p){ renderRoleBookDetail(null); return; } $("roleName").value=p.name||""; $("roleAliases").value=(p.aliases||[]).join("、"); $("roleIdentity").value=p.identity||""; $("roleStatus").value=p.status||"active"; $("roleAppearance").value=p.appearance||""; $("rolePersonality").value=p.personality||""; $("roleSpeechStyle").value=p.speech_style||""; $("roleBackground").value=p.background||""; $("roleGoals").value=p.goals||""; $("roleBoundaries").value=p.boundaries||""; $("roleNotes").value=p.notes||""; $("roleIdentityDetail").value=""; renderRoleBookDetail(p); }
function collectRoleProfile(){ const legacy=$("roleIdentityDetail").value.trim(); return {name:$("roleName").value.trim(),aliases:splitRoleAliases($("roleAliases").value),identity:$("roleIdentity").value.trim(),status:$("roleStatus").value.trim()||"active",appearance:$("roleAppearance").value.trim(),personality:$("rolePersonality").value.trim()||legacy,speech_style:$("roleSpeechStyle").value.trim(),background:$("roleBackground").value.trim(),goals:$("roleGoals").value.trim(),boundaries:$("roleBoundaries").value.trim(),notes:$("roleNotes").value.trim()||legacy}; }
function roleDetailLine(label,value){ if(Array.isArray(value)) value=value.join("、"); if(value&&typeof value==="object") value=Object.entries(value).map(([k,v])=>`${k}: ${v}`).join("；"); const text=String(value||"").trim(); return text?`<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(text)}</dd></div>`:""; }
function roleDetailList(label,values){ const items=(values||[]).map(v=>String(v||"").trim()).filter(Boolean); if(!items.length) return ""; return `<section><strong>${escapeHtml(label)}</strong><ul>${items.map(v=>`<li>${escapeHtml(v)}</li>`).join("")}</ul></section>`; }
function renderRoleBookDetail(profile){ const box=$("roleBookDetail"); if(!box) return; if(!profile){ box.innerHTML='<div class="notice small">选择角色后显示档案和自动累积记忆。</div>'; return; } const memory=selectedRoleMemory(profile); const profileRows=[roleDetailLine("别名",profile.aliases),roleDetailLine("身份",profile.identity),roleDetailLine("状态",profile.status),roleDetailLine("外貌",profile.appearance),roleDetailLine("性格",profile.personality),roleDetailLine("说话风格",profile.speech_style),roleDetailLine("背景",profile.background),roleDetailLine("目标",profile.goals),roleDetailLine("禁忌/边界",profile.boundaries),roleDetailLine("补充设定",profile.notes)].filter(Boolean).join(""); const memoryRows=memory?[roleDetailLine("当前状态",memory.current_state),roleDetailLine("情绪/目标",memory.emotion_and_goals),roleDetailLine("已知信息",memory.knowledge_state),roleDetailLine("关系",memory.relationships),roleDetailList("经历",memory.experiences),roleDetailList("近期行动",memory.recent_actions),roleDetailList("关键对话",memory.key_dialogues)].filter(Boolean).join(""):""; box.innerHTML=`<article><h4>${escapeHtml(profile.name||profile.character_id||"未命名角色")}</h4><dl>${profileRows||'<div><dt>档案</dt><dd>暂无结构化档案。</dd></div>'}</dl>${memoryRows?`<h4>自动累积记忆</h4><div class="role-memory-grid">${memoryRows}</div>`:'<div class="notice small">暂无自动累积记忆。</div>'}</article>`; }
function renderConversationList(convs){ renderCards($("conversationList"), convs||[], c=>{ const b=buttonCard(c.title||c.conversation_id, `${c.message_count||0} 条 · ${c.chat_type||"会话"}`, "打开", c.conversation_id===state.currentConversationId); b.onclick=()=>openConversation(c.conversation_id).catch(e=>toast(e.message)); return b; }); }
async function createRole(){ const selected=state.selectedRoleIds[0]||""; const profile=collectRoleProfile(); if(!profile.name) throw new Error("角色名称不能为空"); if(selected){ await api(`/api/roleplay/characters/${enc(selected)}`,{method:"PUT",body:JSON.stringify({profile})}); toast("角色已更新"); } else { const data=await api("/api/roleplay/characters",{method:"POST",body:JSON.stringify({profile})}); state.selectedRoleIds=[(data.profile||{}).character_id].filter(Boolean); toast("角色已创建"); } await loadRoles(); }
async function deleteRole(){ if(!state.selectedRoleIds.length) throw new Error("请选择角色"); await api(`/api/roleplay/characters/${enc(state.selectedRoleIds[0])}`,{method:"DELETE"}); state.selectedRoleIds=[]; toast("角色已删除"); await loadRoles(); }
function newChat(){ state.currentConversationId=""; state.currentConversationRecord={}; state.chatMessages=[]; state.chatBranches=[]; state.activeChatBranchId=""; state.memoryChangeSets=[]; state.selectedMemoryChangeId=""; $("chatSessionTitle").value="角色对话"; $("chatInput").value=""; renderChatMessages(); renderChatBranches(); renderMemoryChanges(); renderRoleplayMemoryDetail(null); }
async function openConversation(id){ const data=await api(`/api/roleplay/conversations/${enc(id)}`); const c=data.conversation||{}; state.currentConversationId=c.conversation_id||id; state.currentConversationRecord=c; state.selectedRoleIds=c.participant_character_ids||[]; state.chatMessages=c.structured_messages||[]; state.memoryChangeSets=c.memory_change_sets||[]; state.selectedMemoryChangeId=""; $("chatSessionTitle").value=c.title||"角色对话"; $("chatType").value=c.chat_type||"private"; $("replyMode").value=c.reply_mode||"character"; $("senderName").value=c.sender_name||"你"; $("senderProfile").value=c.sender_profile||""; $("requiredResponderIds").value=(c.required_responder_ids||[]).join(","); $("narratorEnabled").checked=!!c.narrator_enabled; $("chatTitle").textContent=c.title||"角色聊天"; renderRoleList(); renderMemoryChanges(); await loadChatControls(id).catch(()=>{}); await loadChatBranches(id).catch(()=>{}); await loadChatMemory().catch(()=>{}); renderChatMessages(); }
function renderChatBranches(){ const sel=$("chatBranchSelect"); if(!sel) return; sel.innerHTML=""; for(const branch of state.chatBranches||[]){ const opt=document.createElement("option"); opt.value=branch.branch_id||""; opt.textContent=`${branch.title||branch.branch_id||"分支"}${branch.parent_branch_id?" · fork":""}`; opt.selected=(branch.branch_id||"")===state.activeChatBranchId; sel.appendChild(opt); } }
async function loadChatBranches(id=state.currentConversationId){ if(!id) { state.chatBranches=[]; state.activeChatBranchId=""; renderChatBranches(); return; } const data=await api(`/api/roleplay/conversations/${enc(id)}/branches`); state.chatBranches=data.branches||[]; state.activeChatBranchId=data.active_branch_id||"main"; renderChatBranches(); }
async function switchChatBranch(){ if(!state.currentConversationId) return; const branchId=$("chatBranchSelect").value; if(!branchId || branchId===state.activeChatBranchId) return; const data=await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/branches/${enc(branchId)}/activate`,{method:"POST",body:"{}"}); const c=data.conversation||{}; state.activeChatBranchId=c.active_branch_id||branchId; state.chatMessages=c.structured_messages||[]; await loadChatBranches(state.currentConversationId); renderChatMessages(); toast("会话分支已切换"); }
async function forkChatBranch(){ if(!state.currentConversationId) await saveConversation(); const last=(state.chatMessages||[]).slice(-1)[0]||{}; const title=prompt("分支标题", `分支 ${(state.chatBranches||[]).length+1}`) || ""; const data=await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/branches/fork`,{method:"POST",body:JSON.stringify({message_id:last.message_id||"",title})}); state.chatBranches=data.branches||[]; state.activeChatBranchId=data.active_branch_id||""; state.chatMessages=(data.branch||{}).messages||state.chatMessages; renderChatBranches(); renderChatMessages(); toast("已派生并切换到新分支"); }
async function deleteChatBranch(){ if(!state.currentConversationId) throw new Error("请先打开会话"); const branchId=$("chatBranchSelect").value; if(!branchId) throw new Error("请选择分支"); await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/branches/${enc(branchId)}`,{method:"DELETE"}); await openConversation(state.currentConversationId); toast("分支已删除"); }
function renderChatMessages(){ const box=$("chatMessages"); if(!box) return; box.innerHTML=""; if(!state.chatMessages.length){ box.innerHTML='<div class="notice small">暂无消息</div>'; return; } for(const msg of state.chatMessages){ const div=document.createElement("div"); div.className=`chat-bubble ${msg.role==="user"?"user":"assistant"} ${msg.speaker_id==="sender_behavior"?"behavior":""} ${msg.speaker_id==="narrator"?"narrator":""} ${state.selectedChatMessageId===(msg.message_id||"")?"selected":""}`; const id=msg.message_id||""; div.innerHTML=`<strong>${escapeHtml(msg.speaker_name||msg.role)}</strong><p>${escapeHtml(msg.content||"")}</p>${msg.action?`<small>${escapeHtml(msg.action)}</small>`:""}<div class="chat-message-actions"><button type="button" data-act="source">来源</button><button type="button" data-act="changes">变更</button><button type="button" data-act="fork">分叉</button><button type="button" data-act="edit">编辑</button><button type="button" data-act="delete">删除</button>${msg.role==="assistant"?'<button type="button" data-act="regenerate">重生成</button>':""}</div>`; div.querySelectorAll("[data-act]").forEach(btn=>btn.onclick=ev=>{ ev.stopPropagation(); handleChatMessageAction(btn.dataset.act,id,msg).catch(e=>toast(e.message)); }); div.onclick=()=>{ state.selectedChatMessageId=id; renderChatMessages(); };
box.appendChild(div); } box.scrollTop=box.scrollHeight; }
async function handleChatMessageAction(action,id,msg){ if(!state.currentConversationId) throw new Error("请先打开会话"); if(!id) throw new Error("消息缺少 ID"); state.selectedChatMessageId=id; if(action==="source"||action==="changes"){ const data=await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/messages/${enc(id)}`); if(action==="changes") state.memoryChangeSets=data.memory_change_sets||state.memoryChangeSets; renderRoleplayDataDetail(action==="source"?"消息来源":"消息关联记忆变更", action==="source"?{source:data.source,message:data.message}:data); renderMemoryChanges(); renderChatMessages(); return; } if(action==="edit"){ const content=prompt("编辑消息内容", msg.content||""); if(content===null) return; const data=await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/messages/${enc(id)}`,{method:"PUT",body:JSON.stringify({content})}); state.chatMessages=((data.conversation||{}).structured_messages)||state.chatMessages; renderChatMessages(); toast("消息已保存"); return; } if(action==="delete"){ if(!confirm("删除该消息？")) return; const data=await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/messages/${enc(id)}`,{method:"DELETE"}); state.chatMessages=((data.conversation||{}).structured_messages)||[]; renderChatMessages(); toast("消息已删除"); return; } if(action==="fork"){ const title=prompt("分支标题", `分支 ${(state.chatBranches||[]).length+1}`)||""; const data=await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/messages/${enc(id)}/fork`,{method:"POST",body:JSON.stringify({title})}); state.chatBranches=data.branches||[]; state.activeChatBranchId=data.active_branch_id||""; state.chatMessages=(data.branch||{}).messages||state.chatMessages; renderChatBranches(); renderChatMessages(); toast("已从该消息分叉"); return; } if(action==="regenerate"){ const requirement=prompt("附加语气/行为要求", "严格符合人物设定和当前视角")||""; const data=await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/messages/${enc(id)}/regenerate`,{method:"POST",body:JSON.stringify({requirement})}); connectTask(data.task_id,payload=>{ const result=(payload.data||{}).result; if(result&&result.messages){ state.chatMessages=result.messages; state.currentConversationId=((result.conversation||{}).conversation_id)||state.currentConversationId; renderChatMessages(); loadChatBranches(state.currentConversationId).catch(()=>{}); } }); selectSection("tasks"); } }
function collectSceneState(){ return {time:$("sceneTime").value,location:$("sceneLocation").value,weather:$("sceneWeather").value,objective:$("sceneObjective").value,description:$("sceneDescription").value,tags:parseIdList($("sceneTags").value),present_character_ids:parseIdList($("scenePresentIds").value)}; }
function fillSceneState(scene={}){ $("sceneTime").value=scene.time||""; $("sceneLocation").value=scene.location||""; $("sceneWeather").value=scene.weather||""; $("sceneObjective").value=scene.objective||""; $("sceneDescription").value=scene.description||""; $("sceneTags").value=(scene.tags||[]).join(","); $("scenePresentIds").value=(scene.present_character_ids||[]).join(","); }
function collectTurnPolicy(){ return {required_speaker_ids:parseIdList($("requiredResponderIds").value),allowed_speaker_ids:parseIdList($("turnAllowedIds").value),blocked_speaker_ids:parseIdList($("turnBlockedIds").value),speaker_order:parseIdList($("turnSpeakerOrder").value),max_speakers:Number($("turnMaxSpeakers").value||0)}; }
function fillTurnPolicy(policy={}){ $("turnAllowedIds").value=(policy.allowed_speaker_ids||[]).join(","); $("turnBlockedIds").value=(policy.blocked_speaker_ids||[]).join(","); $("turnSpeakerOrder").value=(policy.speaker_order||[]).join(","); $("turnMaxSpeakers").value=policy.max_speakers||0; }
function collectChatControlState(){ return {chat_type:$("chatType").value,reply_mode:$("replyMode").value,participant_character_ids:state.selectedRoleIds,required_responder_ids:parseIdList($("requiredResponderIds").value),sender_name:$("senderName").value||"你",sender_profile:$("senderProfile").value,sender_profile_id:$("senderProfileSelect").value,scene_state:collectSceneState(),turn_policy:collectTurnPolicy(),narrator_enabled:$("narratorEnabled").checked,active_branch_id:state.activeChatBranchId||"main"}; }
function fillChatControlState(stateData={}){ $("senderProfileSelect").value=stateData.sender_profile_id||""; fillSceneState(stateData.scene_state||{}); fillTurnPolicy(stateData.turn_policy||{}); if(stateData.required_responder_ids) $("requiredResponderIds").value=(stateData.required_responder_ids||[]).join(","); }
async function loadChatControls(id){ if(!id) return; const data=await api(`/api/roleplay/conversations/${enc(id)}/controls`); state.senderProfiles=data.sender_profiles||state.senderProfiles; state.scenePresets=data.scene_presets||state.scenePresets; renderRoleControls(); fillChatControlState(data.state||{}); }
async function saveChatControls(){ if(!state.currentConversationId) await saveConversation(); const data=await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/controls`,{method:"PUT",body:JSON.stringify({state:collectChatControlState()})}); fillChatControlState(data.state||{}); toast("控制状态已保存"); }
async function saveSenderProfile(){ const profile={name:$("senderName").value||"发送者",identity:"",personality:$("senderProfile").value,notes:$("senderProfile").value}; const id=$("senderProfileSelect").value; const data=id?await api(`/api/roleplay/senders/${enc(id)}`,{method:"PUT",body:JSON.stringify({profile})}):await api("/api/roleplay/senders",{method:"POST",body:JSON.stringify({profile})}); await loadRoles(); $("senderProfileSelect").value=(data.profile||{}).sender_profile_id||id; toast("发送者档案已保存"); }
function applySenderProfile(){ const item=state.senderProfiles.find(p=>p.sender_profile_id===$("senderProfileSelect").value); if(!item) return; $("senderName").value=item.name||"你"; $("senderProfile").value=[item.identity,item.personality,item.appearance,item.background,item.relationships,item.knowledge_state,item.notes].filter(Boolean).join("\n"); }
async function deleteSenderProfile(){ const id=$("senderProfileSelect").value; if(!id) throw new Error("请选择发送者档案"); await api(`/api/roleplay/senders/${enc(id)}`,{method:"DELETE"}); await loadRoles(); toast("发送者档案已删除"); }
async function saveScenePreset(){ const id=$("scenePresetSelect").value; const name=$("sceneLocation").value||$("sceneObjective").value||"场景"; const preset={name,scene:collectSceneState()}; const data=id?await api(`/api/roleplay/scenes/${enc(id)}`,{method:"PUT",body:JSON.stringify({preset})}):await api("/api/roleplay/scenes",{method:"POST",body:JSON.stringify({preset})}); await loadRoles(); $("scenePresetSelect").value=(data.preset||{}).scene_preset_id||id; toast("场景预设已保存"); }
function applyScenePreset(){ const item=state.scenePresets.find(p=>p.scene_preset_id===$("scenePresetSelect").value); if(item) fillSceneState(item.scene||{}); }
async function deleteScenePreset(){ const id=$("scenePresetSelect").value; if(!id) throw new Error("请选择场景预设"); await api(`/api/roleplay/scenes/${enc(id)}`,{method:"DELETE"}); await loadRoles(); toast("场景预设已删除"); }
async function sendRoleChat(){ const message=$("chatInput").value.trim(); if(!message) throw new Error("请输入消息"); const controls=collectChatControlState(); const body={title:$("chatSessionTitle").value||"角色对话",message,character_ids:state.selectedRoleIds,conversation_id:state.currentConversationId,chat_type:controls.chat_type,sender_name:controls.sender_name,sender_profile:controls.sender_profile,sender_profile_id:controls.sender_profile_id,scene_state:controls.scene_state,turn_policy:controls.turn_policy,required_responder_ids:controls.required_responder_ids,reply_mode:$("replyMode").value,narrator_enabled:controls.narrator_enabled}; const data=await api("/api/roleplay/chat",{method:"POST",body:JSON.stringify(body)}); connectTask(data.task_id, payload=>{ const result=(payload.data||{}).result; if(result&&result.messages){ state.currentConversationId=result.conversation_id; state.chatMessages=result.messages; $("chatInput").value=""; renderChatMessages(); loadChatBranches(state.currentConversationId).catch(()=>{}); loadChatMemory().catch(()=>{}); loadChatControls(state.currentConversationId).catch(()=>{}); loadRoles().catch(()=>{}); } }); selectSection("tasks"); }
function buildRoleplayRecord(){ const controls=collectChatControlState(); const existing=state.currentConversationRecord||{}; const activeId=state.activeChatBranchId||existing.active_branch_id||"main"; let branches=(state.chatBranches||[]).map(item=>({...item,messages:[...(item.messages||[])]})); let active=branches.find(item=>(item.branch_id||"")===activeId); if(!active){ active={branch_id:activeId,title:activeId==="main"?"主线":"当前分支",messages:[],timeline:existing.timeline||[],created_at:existing.created_at||""}; branches.push(active); } active.messages=[...(state.chatMessages||[])]; return {...existing,...controls,conversation_id:state.currentConversationId,title:$("chatSessionTitle").value||existing.title||"角色对话",messages:existing.messages||[],structured_messages:state.chatMessages,branches,active_branch_id:activeId,memory_change_sets:state.memoryChangeSets||[],primary_character_id:existing.primary_character_id||state.selectedRoleIds[0]||"",timeline_id:existing.timeline_id||state.currentConversationId||""}; }
async function saveConversation(){ const record=buildRoleplayRecord(); const data=await api("/api/roleplay/conversations",{method:"POST",body:JSON.stringify({record})}); state.currentConversationId=data.conversation_id; state.currentConversationRecord={...record,conversation_id:data.conversation_id}; toast("会话已保存"); await loadChatBranches(state.currentConversationId).catch(()=>{}); await loadChatMemory().catch(()=>{}); await loadRoles(); }
async function deleteConversation(){ if(!state.currentConversationId) throw new Error("请先打开会话"); await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}`,{method:"DELETE"}); newChat(); toast("会话已删除"); await loadRoles(); }
async function exportConversation(){ if(!state.currentConversationId) throw new Error("请先打开会话"); const fmt=$("chatExportFormat")?$("chatExportFormat").value:"txt"; const data=await api(`/api/roleplay/conversations/${enc(state.currentConversationId)}/export`,{method:"POST",body:JSON.stringify({fmt})}); addDownload(data.download); toast("会话导出已生成"); }
async function loadSettings(){ const [base,agent,presetData]=await Promise.all([api("/api/settings"),api("/api/settings/agent-embedding"),api("/api/settings/presets")]); const text=(base.api||{}).text||{}; const image=(base.api||{}).image||{}; const baseSettings=base.settings||{}; $("baseUrl").value=text.base_url||""; $("apiModel").value=text.model||""; $("currentModel").value=text.model||baseSettings.last_model||""; renderModelOptions(baseSettings,text); $("imageBaseUrl").value=image.base_url||""; $("imageModel").value=image.model||""; $("globalUserPrompt").value=baseSettings.global_user_prompt||""; const s=(agent.settings||{}); $("agentGenerationMode").value=s.novel_generation_mode||"classic"; $("agentSkillsEnabled").checked=s.agent_skills_enabled!==false; $("agentRuntimeBackend").value=s.agent_runtime_backend||"legacy"; $("retrievalBackend").value=s.retrieval_backend||"classic"; $("retrievalDefaultLimit").value=s.retrieval_default_limit??8; $("retrievalMinScore").value=s.retrieval_min_score??0; $("retrievalKeywordWeight").value=s.retrieval_keyword_weight??55; $("retrievalSemanticWeight").value=s.retrieval_semantic_weight??45; $("frameworkAutoFallback").checked=s.framework_auto_fallback!==false; $("embeddingBaseUrl").value=s.embedding_base_url||""; $("embeddingModel").value=s.embedding_model||""; $("embeddingBatchSize").value=s.embedding_batch_size??8; $("embeddingTimeoutSeconds").value=s.embedding_timeout_seconds??20; $("embeddingMaxRetries").value=s.embedding_max_retries??1; $("agentWebEnabled").checked=!!s.agent_web_enabled; $("agentWebEndpoint").value=s.agent_web_endpoint||""; $("agentWebMethod").value=s.agent_web_method||"POST"; $("agentWebTimeoutSeconds").value=s.agent_web_timeout_seconds||15; $("agentWebAuthHeader").value=s.agent_web_auth_header||"Authorization"; $("agentWebAuthPrefix").value=s.agent_web_auth_prefix??"Bearer"; $("agentWebQueryField").value=s.agent_web_query_field||"query"; $("agentWebResultsPath").value=s.agent_web_results_path||"results"; $("agentWebTitleField").value=s.agent_web_title_field||"title"; $("agentWebUrlField").value=s.agent_web_url_field||"url"; $("agentWebSnippetField").value=s.agent_web_snippet_field||"content"; $("agentWebMaxResults").value=s.agent_web_max_results||5; state.presets=presetData.presets||{}; state.defaultPresetNames=presetData.default_names||[]; $("themeSelect").value=presetData.theme||"dark"; renderPresetSelect(presetData.current_preset||Object.keys(state.presets)[0]||""); }
function renderModelOptions(settings={},text={}){ const list=$("modelOptions"); if(!list) return; const models=[text.model,settings.last_model,...(settings.favorite_models||[]),...(settings.custom_models||[])].filter(Boolean); list.innerHTML=[...new Set(models)].map(model=>`<option value="${escapeHtml(model)}"></option>`).join(""); }
function renderPresetSelect(selected=""){ const select=$("presetSelect"); if(!select) return; select.innerHTML=""; for(const name of Object.keys(state.presets||{})){ const opt=document.createElement("option"); opt.value=name; opt.textContent=state.defaultPresetNames.includes(name)?`${name} · 默认`:name; select.appendChild(opt); } if(selected && state.presets[selected]) select.value=selected; fillPresetEditor(select.value); }
function fillPresetEditor(name){ const preset=(state.presets||{})[name]||{}; $("presetName").value=name||""; $("presetTemp").value=preset.temp??70; $("presetTopP").value=preset.top_p??90; $("presetFp").value=preset.fp??0; $("presetMaxTokens").value=preset.max_tokens??32768; }
function collectPreset(){ return {temp:Number($("presetTemp").value||70),top_p:Number($("presetTopP").value||90),fp:Number($("presetFp").value||0),max_tokens:Number($("presetMaxTokens").value||32768)}; }
async function savePreset(){ const name=$("presetName").value.trim(); if(!name) throw new Error("请输入预设名称"); const data=await api(`/api/settings/presets/${enc(name)}`,{method:"PUT",body:JSON.stringify({name,preset:collectPreset()})}); state.presets=data.presets||{}; renderPresetSelect(data.current_preset||name); toast("预设已保存"); }
async function setCurrentPreset(){ const name=$("presetSelect").value||$("presetName").value.trim(); if(!name) throw new Error("请选择预设"); const data=await api("/api/settings/presets/current",{method:"PUT",body:JSON.stringify({name})}); renderPresetSelect(data.current_preset||name); toast("已设为当前生成预设"); }
async function deletePreset(){ const name=$("presetName").value.trim()||$("presetSelect").value; if(!name) throw new Error("请选择预设"); await api(`/api/settings/presets/${enc(name)}`,{method:"DELETE"}); toast("预设已删除"); await loadSettings(); }
async function resetPresets(){ await api("/api/settings/presets/reset",{method:"POST",body:"{}"}); toast("预设已恢复默认"); await loadSettings(); }
async function saveTheme(){ await api("/api/settings/theme",{method:"PUT",body:JSON.stringify({theme:$("themeSelect").value})}); toast("主题已保存，桌面端下次加载时生效"); }
async function saveGlobalPrompt(){ const data=await api("/api/settings",{method:"PUT",body:JSON.stringify({settings:{global_user_prompt:$("globalUserPrompt").value}})}); $("globalUserPrompt").value=(data.settings||{}).global_user_prompt||""; toast("全局偏好已保存"); }
async function confirmSensitive(){ const data=await api("/api/auth/confirm",{method:"POST",body:JSON.stringify({password:$("confirmPassword").value})}); state.sensitiveTicket=data.sensitive_ticket; toast("敏感操作已确认"); }
async function saveApi(){ const body={text:{api_key:$("apiKey").value,base_url:$("baseUrl").value,model:$("apiModel").value},image:{api_key:$("imageApiKey").value,base_url:$("imageBaseUrl").value,model:$("imageModel").value}}; await api("/api/settings/api",{method:"PUT",headers:{"X-Sensitive-Ticket":state.sensitiveTicket},body:JSON.stringify(body)}); $("apiKey").value=""; $("imageApiKey").value=""; toast("API 设置已保存"); }
async function saveCurrentModel(){ const model=$("currentModel").value.trim(); if(!model) throw new Error("请输入模型名称"); const data=await api("/api/settings/model",{method:"PUT",body:JSON.stringify({model})}); const text=(data.api||{}).text||{}; $("apiModel").value=text.model||model; $("currentModel").value=data.model||model; renderModelOptions(data.settings||{},text); toast("当前生成模型已切换"); }
async function saveAgentEmbedding(){ const settings={novel_generation_mode:$("agentGenerationMode").value,agent_skills_enabled:$("agentSkillsEnabled").checked,agent_runtime_backend:$("agentRuntimeBackend").value,retrieval_backend:$("retrievalBackend").value,retrieval_default_limit:Number($("retrievalDefaultLimit").value||8),retrieval_keyword_weight:Number($("retrievalKeywordWeight").value||55),retrieval_semantic_weight:Number($("retrievalSemanticWeight").value||45),retrieval_min_score:Number($("retrievalMinScore").value||0),framework_auto_fallback:$("frameworkAutoFallback").checked,embedding_base_url:$("embeddingBaseUrl").value,embedding_api_key:$("embeddingApiKey").value,embedding_model:$("embeddingModel").value,embedding_batch_size:Number($("embeddingBatchSize").value||8),embedding_timeout_seconds:Number($("embeddingTimeoutSeconds").value||20),embedding_max_retries:Number($("embeddingMaxRetries").value||1),agent_web_enabled:$("agentWebEnabled").checked,agent_web_endpoint:$("agentWebEndpoint").value,agent_web_method:$("agentWebMethod").value||"POST",agent_web_timeout_seconds:Number($("agentWebTimeoutSeconds").value||15),agent_web_api_key:$("agentWebApiKey").value,agent_web_auth_header:$("agentWebAuthHeader").value||"Authorization",agent_web_auth_prefix:$("agentWebAuthPrefix").value,agent_web_query_field:$("agentWebQueryField").value||"query",agent_web_results_path:$("agentWebResultsPath").value||"results",agent_web_title_field:$("agentWebTitleField").value||"title",agent_web_url_field:$("agentWebUrlField").value||"url",agent_web_snippet_field:$("agentWebSnippetField").value||"content",agent_web_max_results:Number($("agentWebMaxResults").value||5)}; await api("/api/settings/agent-embedding",{method:"PUT",headers:{"X-Sensitive-Ticket":state.sensitiveTicket},body:JSON.stringify({settings})}); $("embeddingApiKey").value=""; $("agentWebApiKey").value=""; toast("Agent/Embedding 设置已保存"); }
async function testAgentWebSearch(){ await saveAgentEmbedding(); const data=await api("/api/settings/agent-web/test",{method:"POST",body:JSON.stringify({query:"DeepseekAss 搜索测试"})}); toast(`搜索测试成功：${data.count||0} 条结果`); }
async function testEmbedding(){ await saveAgentEmbedding(); const data=await api("/api/settings/embedding/test",{method:"POST",body:"{}"}); toast(`Embedding 测试成功：${data.dimension||0} 维`); }
async function rebuildRetrieval(){ requireBook(); const data=await api(`/api/books/${enc(state.currentBook)}/retrieval/rebuild`,{method:"POST",body:"{}"}); connectTask(data.task_id); selectSection("tasks"); }
async function clearRetrieval(){ requireBook(); await api(`/api/books/${enc(state.currentBook)}/retrieval/clear`,{method:"POST",body:"{}"}); toast("索引已清理"); }
async function changePassword(){ const old_password=$("oldPassword").value; const new_password=$("newPassword").value; const confirm=$("newPasswordConfirm").value; if(new_password!==confirm) throw new Error("两次新密码不一致"); const data=await api("/api/settings/password",{method:"POST",body:JSON.stringify({old_password,new_password})}); if(data.token){ state.token=data.token; sessionStorage.setItem("deepseekass_token",state.token); } $("oldPassword").value=""; $("newPassword").value=""; $("newPasswordConfirm").value=""; toast("密码已修改，会话已刷新"); }
async function exportUserData(){ const data=await api("/api/settings/data/export",{method:"POST",headers:{"X-Sensitive-Ticket":state.sensitiveTicket},body:"{}"}); addDownload(data.download); toast("用户数据包已生成"); selectSection("tasks"); }
async function importUserData(){ const input=$("dataImportFile"); if(!input.files||!input.files[0]) throw new Error("请选择 ZIP 数据包"); if(!confirm("导入会覆盖同名用户数据文件，继续？")) return; const form=new FormData(); form.append("file",input.files[0]); const data=await api("/api/settings/data/import",{method:"POST",headers:{"X-Sensitive-Ticket":state.sensitiveTicket},body:form}); toast(`数据导入完成：${data.imported||0} 个文件`); input.value=""; await bootstrap(); }
async function clearUserData(){ if(!confirm("清空当前用户的书架、对话、设置和日志？此操作不可恢复。")) return; await api("/api/settings/data/clear",{method:"POST",headers:{"X-Sensitive-Ticket":state.sensitiveTicket},body:"{}"}); state.currentBook=""; sessionStorage.removeItem("deepseekass_book"); setCurrentBook(""); toast("用户数据已清空"); await loadBooks(); }
function tokenFilterQuery(){ const params=new URLSearchParams(); const pairs=[["q",$("tokenQuery")?.value],["model",$("tokenModelFilter")?.value],["operation",$("tokenOperationFilter")?.value],["date_from",$("tokenDateFrom")?.value],["date_to",$("tokenDateTo")?.value]]; for(const [key,value] of pairs){ if(value) params.set(key,value); } const query=params.toString(); return query?`?${query}`:""; }
function renderTokenFilterOptions(facets={}){ const modelSel=$("tokenModelFilter"); const opSel=$("tokenOperationFilter"); if(modelSel){ const current=modelSel.value; modelSel.innerHTML='<option value="">全部模型</option>'+(facets.models||[]).map(v=>`<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join(""); modelSel.value=current; } if(opSel){ const current=opSel.value; opSel.innerHTML='<option value="">全部任务</option>'+(facets.operations||[]).map(v=>`<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join(""); opSel.value=current; } }
function formatEstimatedCost(cost={}){ const value=Number(cost.total_cost||0); const currency=cost.currency||"USD"; return `${currency} ${value.toFixed(4)}`; }
function formatDurationMs(value){ return value===null||value===undefined||value==="" ? "-" : `${(Number(value||0)/1000).toFixed(1)}s`; }
function formatTokenCount(value){ return value===null||value===undefined ? "-" : value; }
function tokenGroupCards(title, groups){ const rows=Object.entries(groups||{}).sort((a,b)=>String(b[0]).localeCompare(String(a[0]))).slice(0,8); if(!rows.length) return `<div class="tool-box"><h3>${title}</h3><div class="notice small">暂无数据</div></div>`; return `<div class="tool-box"><h3>${title}</h3><div class="data-table compact-table"><table><thead><tr><th>名称</th><th>次数</th><th>输入</th><th>输出</th><th>总计</th><th>耗时</th></tr></thead><tbody>${rows.map(([name,row])=>`<tr><td>${escapeHtml(name)}</td><td>${row.count||0}</td><td>${row.prompt_tokens||0}</td><td>${row.completion_tokens||0}</td><td>${row.total_tokens||0}</td><td>${formatDurationMs(row.duration_ms||0)}</td></tr>`).join("")}</tbody></table></div></div>`; }
async function loadTokens(){ const data=await api(`/api/token-log${tokenFilterQuery()}`); renderTokenFilterOptions(data.facets||{}); const summary=data.summary||{}; const sum=summary.totals||{}; const activity=summary.activity||{}; const cost=summary.estimated_cost||{}; $("tokenSummary").innerHTML=[["总输入",sum.prompt_tokens||0],["总输出",sum.completion_tokens||0],["总计",sum.total_tokens||0],["总耗时",formatDurationMs(activity.duration_ms||0)],["字符",activity.char_count||0],["汉字",activity.hanzi_count||0],["估算费用",formatEstimatedCost(cost)],["筛选记录",data.total||0],["全部记录",data.overall_total??data.total??0]].map(([k,v])=>`<div class="metric-card"><span>${k}</span><b>${v}</b></div>`).join(""); const groupBox=$("tokenGroupSummary"); if(groupBox) groupBox.innerHTML=tokenGroupCards("按日期",summary.by_date)+tokenGroupCards("按模型",summary.by_model)+tokenGroupCards("按任务类型",summary.by_operation); const rows=data.entries||[]; $("tokenTable").innerHTML=`<table><thead><tr><th>时间</th><th>操作</th><th>方向</th><th>策略</th><th>模型</th><th>输入</th><th>输出</th><th>总计</th><th>耗时</th><th>字符</th><th>汉字</th><th>预览</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${escapeHtml(r.timestamp)}</td><td>${escapeHtml(r.operation)}</td><td>${escapeHtml(r.direction||"")}</td><td>${escapeHtml(r.strategy||"")}</td><td>${escapeHtml(r.model)}</td><td>${formatTokenCount(r.prompt_tokens)}</td><td>${formatTokenCount(r.completion_tokens)}</td><td>${formatTokenCount(r.total_tokens)}</td><td>${formatDurationMs(r.duration_ms)}</td><td>${formatTokenCount(r.char_count)}</td><td>${formatTokenCount(r.hanzi_count)}</td><td>${escapeHtml(r.content_preview||"")}</td></tr>`).join("")}</tbody></table>`; }
async function clearTokens(){ if(confirm("清空 Token 日志？")){ await api("/api/token-log",{method:"DELETE"}); await loadTokens(); } }
async function exportTokens(){ const data=await api(`/api/token-log/export${tokenFilterQuery()}`,{method:"POST",body:"{}"}); addDownload(data.download); toast("Token 日志已生成下载"); }
async function resetTokenFilters(){ for(const id of ["tokenQuery","tokenModelFilter","tokenOperationFilter","tokenDateFrom","tokenDateTo"]){ const el=$(id); if(el) el.value=""; } await loadTokens(); }
function detailObject(value) {
  if (!value || typeof value !== "object") return {};
  return value;
}

function detailText(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function metricHtml(label, value) {
  return `<div class="metric-card"><span>${escapeHtml(label)}</span><b>${escapeHtml(detailText(value))}</b></div>`;
}

function detailPre(title, value) {
  const empty = value === null || value === undefined || value === "" || (typeof value === "object" && !Object.keys(value).length);
  if (empty) return "";
  return `<details class="json-drawer task-json-drawer"><summary>${escapeHtml(title)}</summary><pre class="chapter-record-pre">${escapeHtml(detailText(value))}</pre></details>`;
}

function renderTaskDetail(task = {}) {
  const metadata = detailObject(task.metadata);
  const result = task.result !== undefined ? task.result : task.output;
  const events = Array.isArray(task.events) ? task.events : [];
  const eventRows = events.slice(-30).map((event, index) => {
    const type = event.type || event.event_type || event.status || `事件 ${index + 1}`;
    const message = event.message || event.stage || "";
    const payload = event.data || event.payload || {};
    return `
      <article class="task-event-card">
        <strong>${escapeHtml(type)}</strong>
        <small>${escapeHtml(event.timestamp || event.created_at || "")}</small>
        ${message ? `<p>${escapeHtml(message)}</p>` : ""}
        ${Object.keys(detailObject(payload)).length ? detailPre("事件数据", payload) : ""}
      </article>
    `;
  }).join("");
  return `
    <section class="task-detail-view">
      <div class="task-detail-metrics">
        ${metricHtml("状态", task.status || "-")}
        ${metricHtml("阶段", task.stage || "-")}
        ${metricHtml("进度", `${task.progress || 0}%`)}
        ${metricHtml("可重试", task.retryable ? "是" : "否")}
      </div>
      <div class="tool-box task-detail-main">
        <h3>${escapeHtml(task.name || task.task_id || "任务详情")}</h3>
        <p class="muted">${escapeHtml(task.task_id || "")}</p>
        ${task.error ? `<div class="notice error-box">${escapeHtml(task.error)}</div>` : ""}
        ${task.message ? `<p>${escapeHtml(task.message)}</p>` : ""}
      </div>
      ${detailPre("结果", result)}
      ${detailPre("元数据", metadata)}
      ${eventRows ? `<div class="tool-box"><h3>最近事件</h3><div class="task-event-list">${eventRows}</div></div>` : ""}
    </section>
  `;
}

function renderDiagnostics(data = {}) {
  const downloads = Array.isArray(data.downloads) ? data.downloads : [];
  const tasks = Array.isArray(data.tasks) ? data.tasks : [];
  const settings = data.settings || data.environment || {};
  return `
    <section class="task-detail-view">
      <div class="task-detail-metrics">
        ${metricHtml("用户", data.username || data.user || "-")}
        ${metricHtml("书籍", data.books_count ?? (data.books || []).length ?? "-")}
        ${metricHtml("任务", tasks.length)}
        ${metricHtml("下载", downloads.length)}
      </div>
      ${detailPre("运行状态", settings)}
      ${detailPre("最近任务", tasks.slice(0, 20))}
      ${detailPre("下载记录", downloads.slice(0, 20))}
      ${detailPre("原始诊断", data)}
    </section>
  `;
}
async function loadTasks(){ const data=await api("/api/tasks"); const tasks=data.tasks||[]; const list=$("taskList"); list.innerHTML=""; if(!tasks.length){ list.innerHTML='<div class="notice small">暂无任务历史</div>'; return; } for(const t of tasks){ const row=document.createElement("div"); row.className="item-card task-row"; const status=`${t.status||""} · ${t.stage||""} · ${t.progress||0}%`; row.innerHTML=`<span><strong>${escapeHtml(t.name||t.task_id)}</strong><small>${escapeHtml(status)}${t.error?` · ${escapeHtml(t.error)}`:""}</small></span><span class="task-actions"><button type="button" data-act="detail">详情</button>${t.status==="running"?'<button type="button" data-act="cancel">取消</button>':""}${t.retryable&&["failed","cancelled"].includes(t.status)?'<button type="button" data-act="retry">重试</button>':""}</span>`; row.querySelector('[data-act="detail"]').onclick=ev=>{ ev.stopPropagation(); showTaskDetail(t.task_id).catch(e=>toast(e.message)); };
const cancel=row.querySelector('[data-act="cancel"]'); if(cancel) cancel.onclick=ev=>{ ev.stopPropagation(); cancelTask(t.task_id).catch(e=>toast(e.message)); };
const retry=row.querySelector('[data-act="retry"]'); if(retry) retry.onclick=ev=>{ ev.stopPropagation(); retryTask(t.task_id).catch(e=>toast(e.message)); };
row.onclick=()=>showTaskDetail(t.task_id).catch(e=>toast(e.message)); list.appendChild(row); } }
async function showTaskDetail(id){ const data=await api(`/api/tasks/${enc(id)}`); $("taskDetail").innerHTML=renderTaskDetail(data.task||{}); }
async function cancelTask(id){ const data=await api(`/api/tasks/${enc(id)}/cancel`,{method:"POST",body:"{}"}); toast(data.ok?"任务已请求取消":"任务不可取消或已结束"); await loadTasks(); }
async function retryTask(id){ const data=await api(`/api/tasks/${enc(id)}/retry`,{method:"POST",body:"{}"}); toast("已提交重试任务"); connectTask(data.task_id); await loadTasks(); }
async function loadDiagnostics(){ const data=await api("/api/diagnostics"); $("taskDetail").innerHTML=renderDiagnostics(data); }
async function exportDiagnostics(){ const data=await api("/api/diagnostics/export",{method:"POST",body:"{}"}); addDownload(data.download); toast("诊断信息已生成下载"); selectSection("tasks"); }
function connectTask(id, onPayload){ if(state.eventSource) state.eventSource.close(); state.streamBuffer=""; $("streamText").textContent=""; state.eventSource=new EventSource(`/api/tasks/${enc(id)}/events?token=${enc(state.token)}`); const h=e=>{ const payload=JSON.parse(e.data); updateTask(payload); if(onPayload) onPayload(payload); };
for(const t of ["started","progress","completed","failed","cancelled","finished"]) state.eventSource.addEventListener(t,h); }
function updateTask(p){ const d=p.data||{}; const percent=Number(d.progress||(p.type==="completed"?100:0)); $("taskStage").textContent=d.stage||p.type; $("taskPercent").textContent=`${percent}%`; $("taskBar").style.width=`${percent}%`; $("taskMessage").textContent=p.message||""; if(d.text){ state.streamBuffer=(state.streamBuffer+d.text).slice(-10000); $("streamText").textContent=state.streamBuffer; } if(d.result){ $("streamText").textContent=typeof d.result==="string"?d.result:JSON.stringify(d.result,null,2); } if(["completed","failed","cancelled"].includes(p.type)&&state.eventSource){ state.eventSource.close(); loadTasks().catch(()=>{}); } }
function bind(id, fn, event="click"){ const el=$(id); if(el) el.addEventListener(event, ev=>Promise.resolve(fn(ev)).catch(e=>toast(e.message))); }
$("loginForm").onsubmit=async e=>{ e.preventDefault(); try{ const data=await api("/api/auth/login",{method:"POST",body:JSON.stringify({username:$("loginUsername").value,password:$("loginPassword").value})}); state.token=data.token; sessionStorage.setItem("deepseekass_token",state.token); await bootstrap(); }catch(err){ $("loginError").textContent=err.message; } };
bind("logoutBtn", async()=>{ try{ await api("/api/auth/logout",{method:"POST",body:"{}"}); }catch{} state.token=""; sessionStorage.removeItem("deepseekass_token"); setAuthed(false); });
document.querySelectorAll(".rail-nav button").forEach(btn=>btn.onclick=()=>selectSection(btn.dataset.section));
document.querySelectorAll(".workspace-tabs button").forEach(btn=>{ if(!btn.dataset.cont) btn.onclick=()=>selectWorkspace(btn.dataset.workspace); });
document.querySelectorAll(".continuation-tabs button").forEach(btn=>btn.onclick=()=>selectContinuationTab(btn.dataset.cont));
$("createBookForm").onsubmit=async e=>{ e.preventDefault(); const title=$("newBookTitle").value.trim(); if(!title) return; await api("/api/books",{method:"POST",body:JSON.stringify({title})}); setCurrentBook(title); await loadBooks(); await loadMeta(); };
$("metaForm").onsubmit=async e=>{ e.preventDefault(); await saveMeta(); };
bind("refreshBooksBtn", loadBooks);
bind("renameBookBtn", renameCurrentBook);
bind("deleteBookBtn", deleteCurrentBook);
bind("saveMetaBtn", saveMeta);
bind("contextPreviewBtn", contextPreview);
bind("generateBtn", startGeneration);
bind("contBookSelect", selectContinuationBook, "change");
bind("contLoadMetaBtn", loadContinuationMeta);
bind("contCreateBookBtn", createContinuationBook);
bind("contRenameBookBtn", renameContinuationBook);
bind("contDeleteBookBtn", deleteContinuationBook);
bind("contSaveMetaBtn", saveContinuationMeta);
bind("contOpenChaptersBtn", ()=>openContinuationWorkspace("chapters"));
bind("contOpenWorldBtn", ()=>openContinuationWorkspace("world"));
bind("contMetaXpMode", ev=>syncContinuationXp(ev.target.checked), "change");
bind("contAnalyzeXpMode", ev=>syncContinuationXp(ev.target.checked), "change");
bind("contXpMode", ev=>syncContinuationXp(ev.target.checked), "change");
bind("refreshChaptersBtn", loadChapters);
bind("treeSelect", switchChapterTree, "change");
bind("chapterGraphZoomOutBtn", ()=>changeChapterGraphZoom(-0.15));
bind("chapterGraphResetBtn", resetChapterGraphZoom);
bind("chapterGraphZoomInBtn", ()=>changeChapterGraphZoom(0.15));
bind("chapterGraphFitBtn", fitChapterGraph);
bind("activePathBtn", async()=>{ requireBook(); const data=await api(`/api/books/${enc(state.currentBook)}/active-path`); setChapterInspector("活跃路径", chapterPathHtml(data.nodes||[])); });
bind("clearChapterInspectorBtn", clearChapterInspector);
bind("switchBranchBtn", switchSelectedBranch);
bind("deleteNodeBtn", deleteSelectedNode);
bind("saveChapterContentBtn", saveChapterContent);
bind("saveNodeSummaryBtn", saveNodeSummary);
bind("contextNodePathBtn", showNodePath);
bind("nodeRecordBtn", showNodeRecord);
bind("polishNodeBtn", ()=>generateNodeVariant("polish"));
bind("rewriteNodeBtn", ()=>generateNodeVariant("rewrite"));
bind("deleteVersionBtn", deleteSelectedVersion);
bind("exportNodeBtn", exportSelectedNode);
bind("extractNodeWorldBtn", extractNodeWorld);
bind("rebuildSummaryBtn", rebuildSummary);
bind("rebuildWorldBtn", rebuildWorld);
bind("exportBookBtn", exportBook);
bind("loadWorldBtn", loadWorld);
bind("newWorldEntityBtn", ()=>{ if(!Array.isArray(state.world[state.worldCategory])){ toast("当前分类为只读视图，请切换到角色/地点等实体分类新增。"); return; } const entity={id:"",name:""}; state.worldIndex=-1; $("worldEntityTitle").textContent="新增实体"; $("worldEntityJson").value=JSON.stringify(entity,null,2); renderWorldEntityFields(entity); setWorldEntityEditable(true); });
bind("syncWorldEntityFormBtn", syncWorldEntityFormToJson);
bind("saveWorldEntityBtn", saveWorldEntity);
bind("deleteWorldEntityBtn", deleteWorldEntity);
bind("saveWorldBtn", saveWorld);
bind("auditWorldBtn", auditWorld);
bind("worldAnalyzeBtn", analyzeWorld);
bind("worldSourceBtn", worldSource);
bind("worldPreviewBtn", worldPreview);
bind("worldFactsBtn", worldFacts);
bind("worldContextPoliciesBtn", loadWorldContextPolicies);
bind("saveWorldContextPoliciesBtn", saveWorldContextPolicies);
bind("worldToggleHiddenBtn", toggleWorldHidden);
bind("worldToggleLockedBtn", toggleWorldLocked);
bind("worldMarkResolvedBtn", markWorldResolved);
bind("worldHideLowBtn", hideLowWorld);
bind("worldLockSettingBtn", lockWorldSetting);
bind("worldAddForeshadowBtn", addWorldForeshadowing);
bind("worldMergeCharactersBtn", mergeWorldCharacters);
bind("worldMergeLocationsBtn", mergeWorldLocations);
bind("worldDuplicatesBtn", reviewWorldDuplicates);
bind("worldRejectDuplicateBtn", rejectWorldDuplicate);
bind("worldUndoMergeBtn", undoWorldMerge);
bind("refreshAgentStateBtn", loadAgentState);
bind("createAgentSessionBtn", createWorkbenchSession);
bind("runAgentSessionBtn", runWorkbenchAgent);
bind("refreshAgentRunBtn", refreshAgentRun);
bind("pauseAgentRunBtn", ()=>controlAgentRun("pause"));
bind("resumeAgentRunBtn", ()=>controlAgentRun("resume"));
bind("cancelAgentRunBtn", ()=>controlAgentRun("cancel"));
bind("approveSelectedChangeBtn", approveSelectedChange);
bind("rejectSelectedChangeBtn", rejectSelectedChange);
bind("deleteAdvisorHistoryBtn", deleteAdvisorHistory);
bind("clearAdvisorHistoryBtn", clearAdvisorHistory);
bind("advisorBtn", askAdvisor);
bind("saveAdvisorAdviceBtn", saveAdvisorAdvice);
bind("advisorWorldBtn", advisorAnswerToWorld);
bind("agentPlanBtn", agentPlan);
bind("agentGenerateBtn", agentGenerate);
bind("polishPlanBtn", polishPlan);
bind("polishGenerateBtn", polishGenerate);
bind("extraPlanBtn", extraPlan);
bind("extraGenerateBtn", extraGenerate);
bind("loadSnapshotsBtn", loadSnapshots);
bind("createSnapshotBtn", createSnapshot);
bind("snapshotStatusBtn", showSnapshotStatus);
bind("restoreSnapshotBtn", restoreSelectedSnapshot);
bind("deleteSnapshotBtn", deleteSelectedSnapshot);
bind("exportBtn", startExport);
bind("segmentBtn", segmentText);
bind("agentSegmentBtn", agentSegmentText);
bind("uploadContFilesBtn", uploadContinuationFiles);
bind("contQuickAnalyzeBtn", quickAnalyzeContinuation);
bind("contQuickGenerateBtn", quickGenerateContinuation);
bind("contQuickDirectionsBtn", quickSuggestContinuation);
bind("saveSectionBtn", saveCurrentSection);
bind("deleteSectionBtn", deleteCurrentSection);
bind("mergeSectionBtn", mergeNextSection);
bind("splitSectionBtn", splitCurrentSection);
bind("goAnalyzeBtn", ()=>selectContinuationTab("analyze"));
bind("importSectionsBtn", importSections);
bind("analyzeContinuationBtn", analyzeContinuation);
bind("continuationSuggestBtn", suggestContinuation);
bind("applyManualDirectionBtn", applyManualDirection);
bind("continuationGenerateBtn", generateContinuation);
bind("loadContinuationRunsBtn", loadContinuationRuns);
bind("refreshContinuationRunsBtn", loadContinuationRuns);
bind("applyRunSettingsBtn", applyContinuationRunSettings);
bind("applyRunDirectionsBtn", applyContinuationRunDirections);
bind("contExportBtn", continuationExport);
bind("loadCharacterBookBtn", loadCharacterBook);
bind("saveCharacterBookBtn", saveCharacterBook);
bind("editMemoryChangeBtn", editMemoryChange);
bind("loadChatTimelineBtn", showChatTimeline);
bind("applyMemoryChangeBtn", ()=>updateMemoryChange("apply"));
bind("rejectMemoryChangeBtn", ()=>updateMemoryChange("reject"));
bind("revertMemoryChangeBtn", ()=>updateMemoryChange("revert"));
bind("loadNoteTreeBtn", loadNoteTree);
bind("loadNoteBtn", ()=>openNote($("notePath").value));
bind("saveNoteBtn", saveNote);
bind("previewNoteBtn", previewNote);
bind("createNoteFolderBtn", createNoteFolder);
bind("renameNoteBtn", renameNote);
bind("deleteNoteBtn", deleteNote);
bind("exportNoteBtn", exportNote);
bind("loadRolesBtn", loadRoles);
bind("createRoleBtn", createRole);
bind("deleteRoleBtn", deleteRole);
bind("newChatBtn", newChat);
bind("sendChatBtn", sendRoleChat);
bind("saveConversationBtn", saveConversation);
bind("deleteConversationBtn", deleteConversation);
bind("exportConversationBtn", exportConversation);
bind("refreshConversationsBtn", loadRoles);
bind("saveChatControlsBtn", saveChatControls);
bind("saveSenderProfileBtn", saveSenderProfile);
bind("applySenderProfileBtn", applySenderProfile);
bind("deleteSenderProfileBtn", deleteSenderProfile);
bind("saveScenePresetBtn", saveScenePreset);
bind("applyScenePresetBtn", applyScenePreset);
bind("deleteScenePresetBtn", deleteScenePreset);
bind("chatBranchSelect", switchChatBranch, "change");
bind("forkChatBranchBtn", forkChatBranch);
bind("deleteChatBranchBtn", deleteChatBranch);
bind("confirmBtn", confirmSensitive);
bind("saveCurrentModelBtn", saveCurrentModel);
bind("saveApiBtn", saveApi);
bind("testApiBtn", ()=>api("/api/settings/test-connection",{method:"POST",body:"{}"}).then(()=>toast("连接成功")));
bind("saveGlobalPromptBtn", saveGlobalPrompt);
bind("saveAgentEmbeddingBtn", saveAgentEmbedding);
bind("saveAgentWebBtn", saveAgentEmbedding);
bind("testAgentWebBtn", testAgentWebSearch);
bind("testEmbeddingBtn", testEmbedding);
bind("rebuildRetrievalBtn", rebuildRetrieval);
bind("clearRetrievalBtn", clearRetrieval);
bind("changePasswordBtn", changePassword);
bind("exportDataBtn", exportUserData);
bind("importDataBtn", importUserData);
bind("clearDataBtn", clearUserData);
bind("setCurrentPresetBtn", setCurrentPreset);
bind("savePresetBtn", savePreset);
bind("deletePresetBtn", deletePreset);
bind("resetPresetsBtn", resetPresets);
bind("saveThemeBtn", saveTheme);
const presetSelect=$("presetSelect"); if(presetSelect) presetSelect.onchange=()=>fillPresetEditor(presetSelect.value);
bind("refreshTokensBtn", loadTokens);
bind("applyTokenFiltersBtn", loadTokens);
bind("resetTokenFiltersBtn", resetTokenFilters);
bind("clearTokensBtn", clearTokens);
bind("exportTokensBtn", exportTokens);
bind("refreshTasksBtn", loadTasks);
bind("loadDiagnosticsBtn", loadDiagnostics);
bind("exportDiagnosticsBtn", exportDiagnostics);
bootstrap();
