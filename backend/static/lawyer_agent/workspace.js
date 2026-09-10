(() => {
  const root = document.querySelector(".workspace");
  if (!root) return;
  const caseId = root.dataset.caseId;
  const storageKey = `lca_conversation_${caseId}`;
  let conversationId = localStorage.getItem(storageKey) || null;
  let workspace = window.__WORKSPACE_BOOT__ || null;

  const NODE_ORDER = [
    "N1_PARSE",
    "N2_ORGANIZE",
    "N3_CONFIRM_EVIDENCE",
    "N4_ANALYZE",
    "N5_CONFIRM_PARTIES",
    "N6_CONFIRM_FACTS",
    "N7_CONFIRM_CLAIMS",
    "N8_WRITE",
    "N9_REVIEW",
  ];

  const WF_STATUS_ZH = {
    WAITING_USER: "等待确认",
    RUNNING: "运行中",
    SUCCEEDED: "已完成",
    PAUSED: "已暂停",
    WAITING_RETRY: "等待重试",
    FAILED: "失败",
  };

  const ACTIVE_SECTION_KEY = `lca_active_section_${caseId}`;

  const els = {
    summaryPlaintiff: document.getElementById("summary-plaintiff"),
    summaryDefendant: document.getElementById("summary-defendant"),
    summaryStage: document.getElementById("summary-stage"),
    summaryReadiness: document.getElementById("summary-readiness"),
    summaryTodoCount: document.getElementById("summary-todo-count"),
    summaryUpdated: document.getElementById("summary-updated"),
    stageLabel: document.getElementById("stage-label"),
    stageStatusLabel: document.getElementById("stage-status-label"),
    stageNextText: document.getElementById("stage-next-text"),
    nextActionLabel: document.getElementById("next-action-label"),
    nextActionDesc: document.getElementById("next-action-desc"),
    btnNextAction: document.getElementById("btn-next-action"),
    actionCenterPanel: document.getElementById("action-center-panel"),
    actionCenterCount: document.getElementById("action-center-count"),
    timelinePanel: document.getElementById("timeline-panel"),
    evidenceModal: document.getElementById("evidence-modal"),
    evidenceModalTitle: document.getElementById("evidence-modal-title"),
    evidenceModalBody: document.getElementById("evidence-modal-body"),
    materialsBody: document.querySelector("#materials-table tbody"),
    pendingPanel: document.getElementById("pending-materials-panel"),
    voidPanel: document.getElementById("void-materials-panel"),
    voidCount: document.getElementById("void-count"),
    btnVoidPoolAdd: document.getElementById("btn-void-pool-add"),
    btnVoidPoolClear: document.getElementById("btn-void-pool-clear"),
    usableCount: document.getElementById("usable-count"),
    pendingCount: document.getElementById("pending-count"),
    disclosure: document.getElementById("material-disclosure"),
    parties: document.getElementById("parties-panel"),
    evidence: document.getElementById("evidence-panel"),
    facts: document.getElementById("facts-panel"),
    claim: document.getElementById("claim-panel"),
    draft: document.getElementById("draft-panel"),
    chatLog: document.getElementById("chat-log"),
    agentMeta: document.getElementById("agent-meta"),
    chatError: document.getElementById("chat-error"),
    uploadError: document.getElementById("upload-error"),
    uploadFeedback: document.getElementById("upload-feedback"),
    parseLoading: document.getElementById("parse-loading"),
    uploadForm: document.getElementById("upload-form"),
    uploadSubmit: document.querySelector("#upload-form button[type='submit']"),
    wfStatus: document.getElementById("wf-status"),
    wfNode: document.getElementById("wf-node"),
    wfPending: document.getElementById("wf-pending"),
    wfBlocking: document.getElementById("wf-blocking"),
    readinessStatus: document.getElementById("readiness-status"),
    readinessBlockers: document.getElementById("readiness-blockers"),
    aiMode: document.getElementById("ai-mode"),
    aiModel: document.getElementById("ai-model"),
    draftModal: document.getElementById("draft-modal"),
    draftBody: document.getElementById("draft-body"),
    draftEvidence: document.getElementById("draft-evidence"),
    draftCitations: document.getElementById("draft-citations"),
    draftPending: document.getElementById("draft-pending"),
    workflowStepper: document.getElementById("workflow-stepper"),
    toastRoot: document.getElementById("toast-root"),
    btnHeaderContinue: document.getElementById("btn-header-continue"),
    confirmDialog: document.getElementById("confirm-dialog"),
    confirmTitle: document.getElementById("confirm-title"),
    confirmMessage: document.getElementById("confirm-message"),
    confirmOk: document.getElementById("confirm-ok"),
  };

  function confirmAction(title, message, options) {
    const opts = options || {};
    if (!els.confirmDialog) return Promise.resolve(window.confirm(message));
    els.confirmTitle.textContent = title || "确认操作";
    els.confirmMessage.textContent = message || "";
    if (els.confirmOk) {
      els.confirmOk.textContent = opts.okLabel || "确认";
      els.confirmOk.classList.toggle("danger", !!opts.danger);
    }
    els.confirmDialog.showModal();
    return new Promise((resolve) => {
      const onClose = () => {
        els.confirmDialog.removeEventListener("close", onClose);
        resolve(els.confirmDialog.returnValue === "ok");
      };
      els.confirmDialog.addEventListener("close", onClose);
    });
  }

  function showToast(message, type) {
    if (!els.toastRoot || !message) return;
    const toast = document.createElement("div");
    toast.className = "toast " + (type || "info");
    toast.textContent = message;
    els.toastRoot.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
  }

  function humanizeWorkflowStatus(status) {
    const s = String(status || "未启动");
    return WF_STATUS_ZH[s] || s;
  }

  function badge(text) {
    return `<span class="badge ${text || ""}">${text || "—"}</span>`;
  }

  function fileIconClass(type) {
    if (type === "pdf") return "pdf";
    if (type === "word") return "word";
    if (type === "md") return "md";
    return "other";
  }

  function fileIconLabel(type) {
    if (type === "pdf") return "PDF";
    if (type === "word") return "DOC";
    if (type === "md") return "MD";
    return String(type || "—").slice(0, 3).toUpperCase();
  }

  function emptyState(text, icon) {
    return `<div class="empty-state"><div class="empty-state-icon">${icon || "—"}</div>${escapeHtml(text)}</div>`;
  }

  function truncateCell(text, maxLen, key) {
    const s = String(text || "");
    if (s.length <= maxLen) return escapeHtml(s);
    return `<span class="cell-truncate" data-expand-key="${escapeHtml(key)}" data-full-text="${escapeHtml(s)}">${escapeHtml(s.slice(0, maxLen))}…</span>
      <button type="button" class="btn-link" data-expand="${escapeHtml(key)}">展开</button>`;
  }

  function roleBadge(p) {
    const role = p.role || "";
    const label = p.role_label || role || "—";
    return `<span class="badge ${escapeHtml(role)}">${escapeHtml(label)}</span>`;
  }

  function workActionBtn(label, actionType, target, variant) {
    const cls = variant === "danger" ? "btn-reject" : variant === "ghost" ? "ghost" : "btn-accept";
    const targetAttr = target != null ? ` data-action-target="${escapeHtml(String(target))}"` : "";
    return `<button type="button" class="btn small ${cls}" data-work-action="${escapeHtml(actionType)}"${targetAttr}>${escapeHtml(label)}</button>`;
  }

  function actionButtons(actionType, rejectType, target) {
    return `${workActionBtn("确认", actionType, target)} ${workActionBtn("拒绝", rejectType, target, "danger")}`;
  }

  function navigateToSection(section) {
    if (!section) return;
    const el = document.getElementById(`section-${section}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      localStorage.setItem(ACTIVE_SECTION_KEY, section);
    }
  }

  function showEvidenceModal(evidence) {
    if (!els.evidenceModal || !els.evidenceModalBody) return;
    const spans = evidence.source_spans || [];
    els.evidenceModalTitle.textContent = `证据${evidence.number}：${evidence.title || ""}`;
    if (!spans.length) {
      els.evidenceModalBody.innerHTML = `<p class="muted">暂无原文摘录。</p><p>${escapeHtml(evidence.summary || "")}</p>`;
    } else {
      els.evidenceModalBody.innerHTML = spans
        .map((s) => {
          const loc =
            s.character_start != null
              ? `字符 ${s.character_start}–${s.character_end}`
              : "—";
          return `<div class="source-excerpt">
            <div><strong>${escapeHtml(s.material_filename || "材料")}</strong> · ${escapeHtml(loc)}</div>
            <blockquote>${escapeHtml(s.quote || "")}</blockquote>
          </div>`;
        })
        .join("");
    }
    els.evidenceModal.showModal();
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function formatWhen(raw) {
    if (!raw) return "—";
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return String(raw);
    const pad = (n) => String(n).padStart(2, "0");
    const now = new Date();
    const sameDay =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    if (sameDay) return `今天 ${hm}`;
    if (d.getFullYear() === now.getFullYear()) {
      return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
    }
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
  }

  function setParseLoading(on, text) {
    const box = els.parseLoading;
    if (!box) return;
    const hint = box.querySelector("[data-parse-loading-text]");
    if (hint && text) hint.textContent = text;
    box.hidden = !on;
    if (els.uploadSubmit) els.uploadSubmit.disabled = on;
    const fileInput = document.getElementById("upload-file");
    if (fileInput) fileInput.disabled = on;
  }

  function humanizeExtractionError(raw) {
    const s = String(raw || "");
    if (!s) return "";
    if (s.includes("PDF_NEEDS_OCR") || s.includes("OCR path required") || s.includes("文字层") ||
        s.includes("CAMSCANNER_")) {
      if (s.includes("node") && (s.includes("not recognized") || s.includes("不是内部"))) {
        return (
          "后端找不到 Node.js。请重启后端服务（确保本机已安装 Node），" +
          "或手动转为 .md / .docx 再上传。"
        );
      }
      if (s.includes("CAMSCANNER_CLI_FAILED") || s.includes("auth login") || s.includes("未登录")) {
        return (
          "扫描件已尝试 CamScanner 转换但失败。请在本机运行 " +
          ".\\node_modules\\.bin\\camscanner-cli.cmd auth login 后重试，" +
          "或手动转为 .md / .docx 再上传。"
        );
      }
      if (s.includes("CAMSCANNER_CLI_NOT_FOUND")) {
        return (
          "未找到 CamScanner CLI。请安装 Node.js 并在项目目录执行 npm install，" +
          "或手动转为 .md / .docx 再上传。"
        );
      }
      return (
        "扫描件，无可提取文字。系统将尝试 CamScanner 转为 Markdown；" +
        "失败时可手动上传 .md / .docx，或导出带文字层的 PDF。"
      );
    }
    if (s.includes("FILE_TOO_LARGE")) {
      return "文件过大。";
    }
    if (s.includes("UNSUPPORTED") || s.includes(".doc is not supported")) {
      return "不支持该格式。请上传 .pdf、.docx 或 .md。";
    }
    return s.replace(/^[A-Z0-9_]+:\s*/, "");
  }

  function renderDisclosure(pool, disclosure) {
    if (!els.disclosure) return;
    const d = disclosure || (pool && pool.disclosure) || null;
    if (!d) {
      els.disclosure.hidden = true;
      els.disclosure.textContent = "";
      return;
    }
    const pending = (pool && pool.pending_count) || d.pending_count || 0;
    if (!pending) {
      els.disclosure.hidden = true;
      els.disclosure.textContent = "";
      els.disclosure.classList.remove("ok");
      return;
    }
    els.disclosure.hidden = false;
    els.disclosure.classList.remove("ok");
    const warning = (pool && pool.warning) || "";
    els.disclosure.textContent = [d.summary, warning].filter(Boolean).join("\n\n");
  }

  function formatMaterialType(m) {
    const name = String(m.filename || "").toLowerCase();
    const mime = String(m.mime || "").toLowerCase();
    if (name.endsWith(".md") || mime.includes("markdown")) return "md";
    if (name.endsWith(".docx") || mime.includes("wordprocessingml")) return "word";
    if (name.endsWith(".pdf") || mime === "application/pdf") return "pdf";
    if (name.includes(".")) return name.split(".").pop();
    return "—";
  }

  function renderUsableMaterials(list) {
    if (!els.materialsBody) return;
    if (!list || !list.length) {
      els.materialsBody.innerHTML =
        `<tr><td colspan="6">${emptyState("暂无已成功读取的案件材料", "📄")}</td></tr>`;
      return;
    }
    els.materialsBody.innerHTML = list
      .map((m) => {
        const pages = m.page_count != null ? m.page_count : "—";
        const fmt = formatMaterialType(m);
        const iconCls = fileIconClass(fmt);
        return `<tr>
          <td><div class="file-cell"><span class="file-icon ${iconCls}">${fileIconLabel(fmt)}</span><span>${escapeHtml(m.filename)}</span></div></td>
          <td>${escapeHtml(fmt)}</td>
          <td>${escapeHtml(formatWhen(m.created_at))}</td>
          <td>${badge("解析成功")}</td>
          <td>${escapeHtml(pages)}</td>
          <td class="row-actions">
            <button type="button" class="btn small danger" data-void-material="${escapeHtml(m.id)}" data-void-name="${escapeHtml(m.filename)}">作废</button>
          </td>
        </tr>`;
      })
      .join("");
  }

  function renderPendingMaterials(list) {
    if (!els.pendingPanel) return;
    const pendingSection = document.getElementById("pending-section");
    if (pendingSection) pendingSection.open = !!(list && list.length);
    if (!list || !list.length) {
      els.pendingPanel.innerHTML = emptyState("没有待处理文件", "📥");
      return;
    }
    els.pendingPanel.innerHTML = list
      .map((m) => {
        const reason =
          m.pending_reason ||
          humanizeExtractionError(m.extraction_error) ||
          "AI 暂时无法读取";
        const ocrNote =
          String(reason).includes("OCR") || String(reason).includes("扫描")
            ? `<div class="muted">暂不支持 OCR · 需重新上传可读取版本</div>`
            : "";
        return `<div class="pending-item">
          <div><strong>${escapeHtml(m.filename)}</strong></div>
          <div>状态：${escapeHtml(m.status_label || "读取失败")}</div>
          <div>原因：${escapeHtml(reason)}</div>
          <div>本轮分析：未参与</div>
          ${ocrNote}
          <div class="actions">
            <button type="button" class="btn small" data-reparse-material="${escapeHtml(m.id)}" data-reparse-name="${escapeHtml(m.filename)}">重新解析</button>
            <button type="button" class="btn small danger" data-void-material="${escapeHtml(m.id)}" data-void-name="${escapeHtml(m.filename)}">作废</button>
          </div>
        </div>`;
      })
      .join("");
  }

  function renderVoidMaterials(list) {
    if (!els.voidPanel) return;
    const voidSection = document.getElementById("void-section");
    if (voidSection) voidSection.open = !!(list && list.length);
    if (!list || !list.length) {
      els.voidPanel.innerHTML = emptyState("作废池为空", "🗑");
      return;
    }
    els.voidPanel.innerHTML = list
      .map((m) => {
        const reason = m.void_reason || "已作废";
        return `<div class="pending-item void-item">
          <div><strong>${escapeHtml(m.filename)}</strong></div>
          <div>状态：已作废</div>
          <div>原因：${escapeHtml(reason)}</div>
          <div class="muted">上传时间：${escapeHtml(formatWhen(m.created_at))}</div>
        </div>`;
      })
      .join("");
  }

  function renderMaterialPool(data) {
    const usable = data.usable_materials || data.materials || [];
    const pending = data.pending_materials || [];
    const voided = data.void_materials || [];
    const pool = data.material_pool || {};
    if (els.usableCount) {
      els.usableCount.textContent = `（${pool.usable_count != null ? pool.usable_count : usable.length}）`;
    }
    if (els.pendingCount) {
      els.pendingCount.textContent = `（${pool.pending_count != null ? pool.pending_count : pending.length}）`;
    }
    if (els.voidCount) {
      els.voidCount.textContent = `（${pool.void_count != null ? pool.void_count : voided.length}）`;
    }
    renderUsableMaterials(usable);
    renderPendingMaterials(pending);
    renderVoidMaterials(voided);
    renderDisclosure(pool, data.analysis_disclosure);
  }

  function renderWorkProduct(wp) {
    if (!wp) return;
    const cs = wp.case_summary || {};
    const stage = wp.stage || {};
    const next = wp.next_action || {};
    const todos = wp.todo_summary || {};
    if (els.summaryPlaintiff) els.summaryPlaintiff.textContent = cs.plaintiff || "—";
    if (els.summaryDefendant) els.summaryDefendant.textContent = cs.defendant || "—";
    if (els.summaryStage) els.summaryStage.textContent = stage.stage_label || "—";
    if (els.summaryTodoCount) {
      els.summaryTodoCount.textContent = `${todos.total_count || 0} 项`;
      els.summaryTodoCount.classList.toggle("has-pending", (todos.total_count || 0) > 0);
    }
    if (els.summaryUpdated) els.summaryUpdated.textContent = formatWhen(cs.updated_at);
    if (els.stageLabel) els.stageLabel.textContent = stage.stage_label || "—";
    if (els.stageStatusLabel) els.stageStatusLabel.textContent = stage.stage_status_label || "—";
    if (els.stageNextText) els.stageNextText.textContent = next.label || "—";
    if (els.nextActionLabel) els.nextActionLabel.textContent = next.label || "—";
    if (els.nextActionDesc) els.nextActionDesc.textContent = next.description || "";
    if (els.btnNextAction) {
      const actionable = next.action_type && !["NAVIGATE", "VIEW_DRAFT", "VIEW_EVIDENCE"].includes(next.action_type);
      els.btnNextAction.hidden = !next.label;
      els.btnNextAction.textContent = actionable ? "立即处理" : "前往查看";
      els.btnNextAction.dataset.workAction = next.action_type || "NAVIGATE";
      els.btnNextAction.dataset.actionTarget = next.action_target || next.section_anchor || "";
      els.btnNextAction.dataset.sectionAnchor = next.section_anchor || "";
    }
    if (els.actionCenterCount) {
      els.actionCenterCount.textContent = todos.total_count ? `（${todos.total_count}）` : "";
    }
    renderActionCenter(todos.items || []);
    renderTimeline(wp.timeline || []);
    const resume = wp.resume || {};
    if (resume.conversation_id && !conversationId) {
      conversationId = resume.conversation_id;
      localStorage.setItem(storageKey, conversationId);
    }
  }

  function renderActionCenter(items) {
    if (!els.actionCenterPanel) return;
    if (!items.length) {
      els.actionCenterPanel.innerHTML = emptyState("当前无紧急待办，可继续补充材料或查看各分区。", "✅");
      return;
    }
    els.actionCenterPanel.innerHTML = items
      .map((item) => {
        const btns = (item.actions || [])
          .map((a) => {
            if (a.action_type === "NAVIGATE") {
              return `<button type="button" class="btn small ghost" data-navigate="${escapeHtml(a.target || item.entity_ref?.section || "")}">${escapeHtml(a.label)}</button>`;
            }
            if (a.action_type === "VIEW_EVIDENCE") {
              return `<button type="button" class="btn small ghost" data-view-evidence="${escapeHtml(a.target || "")}">${escapeHtml(a.label)}</button>`;
            }
            if (a.action_type === "VIEW_DRAFT") {
              return `<button type="button" class="btn small ghost" data-view-draft="1">${escapeHtml(a.label)}</button>`;
            }
            if (a.action_type === "AGENT_MESSAGE") {
              return `<button type="button" class="btn small ghost" data-agent-msg="${escapeHtml(a.target || "")}">${escapeHtml(a.label)}</button>`;
            }
            return workActionBtn(a.label, a.action_type, a.target, a.variant);
          })
          .join(" ");
        return `<div class="todo-item" data-entity-type="${escapeHtml(item.todo_type)}" data-entity-key="${escapeHtml(item.id)}">
          <div class="todo-head"><span class="todo-type">${escapeHtml(item.title)}</span></div>
          <p class="todo-summary">${escapeHtml(item.summary)}</p>
          ${item.reason ? `<p class="todo-reason muted">依据：${escapeHtml(item.reason)}</p>` : ""}
          <div class="row-actions">${btns}</div>
        </div>`;
      })
      .join("");
  }

  function renderTimeline(events) {
    if (!els.timelinePanel) return;
    if (!events.length) {
      els.timelinePanel.innerHTML = emptyState("暂无工作记录，上传材料或开始处理后会出现。", "🕐");
      return;
    }
    els.timelinePanel.innerHTML = `<ul class="timeline-list">${events
      .slice()
      .reverse()
      .map(
        (e) =>
          `<li><span class="timeline-time muted">${escapeHtml(formatWhen(e.occurred_at))}</span> ${escapeHtml(e.summary)}</li>`
      )
      .join("")}</ul>`;
  }

  function renderParties(list) {
    if (!list || !list.length) {
      els.parties.innerHTML = emptyState("暂无当事人，请点击上方「新增当事人」录入原告/被告候选", "👥");
      return;
    }
    els.parties.innerHTML = `<div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>角色</th><th>名称</th><th>状态</th><th></th></tr></thead><tbody>
      ${list
        .map(
          (p) => `<tr>
          <td>${p.display_index}</td>
          <td>${roleBadge(p)}</td>
          <td><strong>${escapeHtml(p.name)}</strong></td>
          <td>${badge(p.status_label || p.layer)}</td>
          <td class="row-actions">
            ${
              p.layer === "CANDIDATE"
                ? actionButtons("CONFIRM_PARTY", "REJECT_PARTY", p.display_index)
                : ""
            }
          </td>
        </tr>`
        )
        .join("")}
    </tbody></table></div>`;
  }

  function renderEvidence(list) {
    if (!list || !list.length) {
      els.evidence.innerHTML = emptyState("暂未整理出证据。请先上传案件材料并运行证据整理。", "📋");
      return;
    }
    els.evidence.innerHTML = `<div class="table-wrap"><table class="table"><thead><tr><th>编号</th><th>名称</th><th>证明目的</th><th>来源材料</th><th>状态</th><th></th></tr></thead><tbody>
      ${list
        .map(
          (e) => `<tr data-entity-type="evidence" data-entity-key="${escapeHtml(e.id)}">
          <td>${escapeHtml(e.number)}</td>
          <td>${escapeHtml(e.title)}</td>
          <td>${truncateCell(e.summary || "", 60, `ev-${e.number}`)}</td>
          <td>${escapeHtml(e.source_material || "—")}</td>
          <td><span class="badge ${escapeHtml(e.acceptance)}">${escapeHtml(e.acceptance_label || e.acceptance)}</span></td>
          <td class="row-actions">
            ${
              e.acceptance === "PENDING"
                ? `${workActionBtn("采纳", "ACCEPT_EVIDENCE", e.number)} ${workActionBtn("排除", "EXCLUDE_EVIDENCE", e.number, "danger")} <button type="button" class="btn small ghost" data-view-evidence="${escapeHtml(e.id)}">查看原文</button>`
                : `<button type="button" class="btn small ghost" data-view-evidence="${escapeHtml(e.id)}">查看原文</button>`
            }
          </td>
        </tr>`
        )
        .join("")}
    </tbody></table></div>`;
  }

  function renderFactSection(title, tagClass, rows, actions) {
    if (!rows.length) return "";
    return `<div class="fact-section ${tagClass}">
      <h3 class="subhead">${escapeHtml(title)} <span class="count-badge">（${rows.length}）</span></h3>
      <div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>事实描述</th><th>状态</th><th></th></tr></thead><tbody>
      ${rows
        .map(
          (f) => `<tr data-entity-type="fact" data-entity-key="${escapeHtml(f.fact_key)}">
          <td>${f.display_index}</td>
          <td>${truncateCell(f.statement, 120, `fact-${f.display_index}`)}</td>
          <td><span class="badge ${escapeHtml(f.status)}">${escapeHtml(f.status_label || f.status)}</span></td>
          <td class="row-actions">${actions(f)}</td>
        </tr>`
        )
        .join("")}
      </tbody></table></div></div>`;
  }

  function renderFacts(list) {
    if (!list || !list.length) {
      els.facts.innerHTML = emptyState("暂未形成待确认案件事实。", "📌");
      return;
    }
    const candidates = list.filter((f) => f.status === "CANDIDATE");
    const confirmed = list.filter((f) => f.status === "CONFIRMED");
    const rejected = list.filter((f) => f.status === "REJECTED");
    els.facts.innerHTML =
      renderFactSection("待确认事实（AI 候选）", "ai-candidate", candidates, (f) =>
        actionButtons("CONFIRM_FACT", "REJECT_FACT", f.display_index)
      ) +
      renderFactSection("律师已确认事实", "lawyer-confirmed", confirmed, () => "") +
      renderFactSection("已拒绝 / 历史事实", "lawyer-rejected", rejected, () => "");
    if (!candidates.length && !confirmed.length && !rejected.length) {
      els.facts.innerHTML = emptyState("暂未形成待确认案件事实。", "📌");
    }
  }

  function renderClaim(c) {
    if (!c) {
      els.claim.innerHTML = emptyState("尚未形成诉请方向。", "⚖");
      return;
    }
    const statusLabel =
      c.status === "CONFIRMED" ? "已确认" : c.status === "CANDIDATE" ? "待确认" : c.status;
    const amountText =
      c.amount != null ? `${c.amount} ${c.currency || "元"}`.trim() : "未主张";
    els.claim.innerHTML = `
      <div class="claim-card" data-entity-type="claim">
        <p><span class="badge ${escapeHtml(c.status)}">${escapeHtml(statusLabel)}</span></p>
        <h3 class="subhead">诉请方向</h3>
        <div class="claim-grid">
          <div class="claim-field full"><span class="label">主诉请</span>${escapeHtml(c.description || "—")} ${c.amount != null ? escapeHtml(String(c.amount)) + " 元" : ""}</div>
          <div class="claim-field"><span class="label">策略</span>${escapeHtml(c.overall_strategy || "—")}</div>
          <div class="claim-field"><span class="label">金额</span>${escapeHtml(amountText)}</div>
          <div class="claim-field full"><span class="label">计算基础</span>${escapeHtml(c.calculation_basis || "—")}</div>
        </div>
        ${
          c.status === "CANDIDATE"
            ? `<div class="row-actions" style="margin-top:0.65rem">${workActionBtn("确认", "CONFIRM_CLAIM")} ${workActionBtn("拒绝", "REJECT_CLAIM", null, "danger")}</div>`
            : ""
        }
      </div>
    `;
  }

  function openDraftModal(d) {
    if (!d || !els.draftModal) return;
    const body = d.body_structured_json || {};
    if (els.draftBody) els.draftBody.textContent = body.full_text || "（暂无正文）";
    if (els.draftEvidence) {
      els.draftEvidence.textContent =
        body.evidence_directory_text ||
        (body.sections && body.sections.evidence) ||
        "（暂无证据目录）";
    }
    if (els.draftCitations) {
      const evIndex = {};
      (body.evidence_directory || []).forEach((item) => {
        const label = `【证据${item.display_number || "?"}】`;
        (item.merged_evidence_refs || [{ evidence_item_id: item.evidence_item_id }]).forEach(
          (ref) => {
            if (ref && ref.evidence_item_id) {
              evIndex[String(ref.evidence_item_id)] = {
                label,
                title: item.title || "",
                purpose: item.proof_purpose || "",
                material: item.material_filename || item.source_label || "",
              };
            }
          }
        );
      });
      const blocks = (body.blocks || [])
        .map((b, i) => {
          const evLabels = (b.evidence_refs || [])
            .map((r) => {
              const meta = evIndex[String(r.evidence_item_id)];
              return meta ? meta.label : "";
            })
            .filter(Boolean);
          return `[段落${i + 1}] ${b.text || ""}` + (evLabels.length ? `\n  证据引用：${evLabels.join("、")}` : "");
        })
        .join("\n\n");
      els.draftCitations.textContent = blocks || "（暂无引用块）";
    }
    if (els.draftPending) {
      const pending = body.pending_fields || [];
      els.draftPending.textContent = pending.length
        ? pending.map((p) => `• ${p}`).join("\n")
        : "暂无待补项";
    }
    document.querySelectorAll(".draft-tab").forEach((t, i) => t.classList.toggle("active", i === 0));
    document.querySelectorAll(".draft-pane").forEach((pane, i) => {
      pane.hidden = i !== 0;
      pane.classList.toggle("active", i === 0);
    });
    els.draftModal.showModal();
  }

  function renderDraft(d) {
    if (!d) {
      els.draft.innerHTML = emptyState("尚未生成起诉状。", "📝");
      return;
    }
    const statusMap = {
      DRAFT: "草稿",
      IN_REVIEW: "待审核",
      APPROVED_BY_LAWYER: "已批准",
    };
    els.draft.innerHTML = `
      <div class="draft-card">
        <p><span class="badge">${escapeHtml(statusMap[d.status] || d.status)}</span> 版本 v${d.version}</p>
        <div class="row-actions">
          <button class="btn small" id="btn-view-draft">查看全文</button>
          ${
            d.status === "DRAFT" || d.status === "IN_REVIEW"
              ? workActionBtn("批准", "APPROVE_DRAFT")
              : ""
          }
          ${workActionBtn("生成/重新生成", "GENERATE_DRAFT", null, "ghost")}
        </div>
      </div>
    `;
    const viewBtn = document.getElementById("btn-view-draft");
    if (viewBtn) viewBtn.onclick = () => openDraftModal(d);
  }

  function renderMarkdownLite(text) {
    const escaped = escapeHtml(text || "");
    return escaped
      .replace(/^### (.+)$/gm, "<strong>$1</strong>")
      .replace(/^## (.+)$/gm, "<strong>$1</strong>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/^&gt; (.+)$/gm, "<blockquote>$1</blockquote>")
      .replace(/^-\s+(.+)$/gm, "• $1")
      .replace(/\n/g, "<br>");
  }

  function renderChat(messages) {
    const list = messages || [];
    if (!list.length) {
      els.chatLog.innerHTML = `<div class="chat-empty">
        <div class="chat-empty-icon">💬</div>
        <p>还没有对话记录</p>
        <p class="muted">试试输入「状态」或「继续」，或使用下方快捷按钮</p>
      </div>`;
      return;
    }
    els.chatLog.innerHTML = list
      .map((m) => {
        const roleLabel = m.role === "USER" ? "律师" : m.role === "AGENT" ? "助手" : m.role;
        const pending = m._pending ? " pending" : "";
        return `<div class="msg ${m.role}${pending}">
          <div class="role">${escapeHtml(roleLabel)} · ${escapeHtml(formatWhen(m.created_at))}</div>
          <div class="msg-body">${renderMarkdownLite(m.content)}</div>
        </div>`;
      })
      .join("");
    els.chatLog.scrollTop = els.chatLog.scrollHeight;
  }

  function renderWorkflowStepper(wf, nodeLabels) {
    if (!els.workflowStepper) return;
    const labels = nodeLabels || (workspace && workspace.node_labels) || {};
    const current = (wf && wf.current_node) || "";
    const currentIdx = NODE_ORDER.indexOf(current);
    els.workflowStepper.innerHTML = NODE_ORDER.map((code, idx) => {
      const label = labels[code] || code;
      const short = label.replace(/^N\d+\s*/, "") || label;
      let state = "pending";
      if (currentIdx >= 0) {
        if (idx < currentIdx) state = "done";
        else if (idx === currentIdx) state = "current";
      } else if (code === current) state = "current";
      const mark = state === "done" ? "✓" : idx + 1;
      return `<div class="step ${state}" title="${escapeHtml(label)}">
        <div class="step-dot">${mark}</div>
        <div class="step-label">${escapeHtml(short)}</div>
      </div>`;
    }).join("");
  }

  function renderHeaderActions(wf) {
    if (!els.btnHeaderContinue) return;
    const waiting = wf && wf.status === "WAITING_USER";
    els.btnHeaderContinue.hidden = !waiting;
  }

  function renderNavBadges(data) {
    const pool = data.material_pool || {};
    const parties = data.parties || [];
    const evidence = data.evidence || [];
    const facts = data.facts || [];
    const pendingParties = parties.filter((p) => p.layer === "CANDIDATE").length;
    const pendingEvidence = evidence.filter((e) => e.acceptance === "PENDING").length;
    const pendingFacts = facts.filter((f) => f.status === "CANDIDATE").length;
    const pendingMaterials = pool.pending_count || (data.pending_materials || []).length;

    const wp = data.work_product || {};
    const todoTotal = (wp.todo_summary && wp.todo_summary.total_count) || 0;
    const readinessPending =
      data.pleading_readiness && data.pleading_readiness.status !== "READY"
        ? (data.pleading_readiness.blocking_issues || []).length
        : 0;
    const counts = {
      "action-center": todoTotal,
      materials: pool.usable_count ?? (data.usable_materials || data.materials || []).length,
      parties: parties.length,
      evidence: evidence.length,
      facts: facts.length,
      claim: data.claim_direction ? 1 : 0,
      readiness: readinessPending,
      draft: data.draft ? 1 : 0,
    };
    const pendingMap = {
      "action-center": todoTotal,
      materials: pendingMaterials,
      parties: pendingParties,
      evidence: pendingEvidence,
      facts: pendingFacts,
      claim: data.claim_direction && data.claim_direction.status === "CANDIDATE" ? 1 : 0,
      readiness: readinessPending,
      draft: data.draft && (data.draft.status === "DRAFT" || data.draft.status === "IN_REVIEW") ? 1 : 0,
    };

    document.querySelectorAll("[data-nav-badge]").forEach((el) => {
      const key = el.dataset.navBadge;
      const n = counts[key];
      const p = pendingMap[key];
      el.textContent = n != null && n > 0 ? n : "";
      el.classList.toggle("pending", p > 0);
    });

    document.querySelectorAll(".section-nav-link[data-nav]").forEach((link) => {
      const key = link.dataset.nav;
      link.classList.toggle("has-pending", (pendingMap[key] || 0) > 0);
    });

    const sectionPending = {
      "section-action-center": todoTotal > 0,
      "section-materials": pendingMaterials > 0,
      "section-parties": pendingParties > 0,
      "section-evidence": pendingEvidence > 0,
      "section-facts": pendingFacts > 0,
      "section-claim": pendingMap.claim > 0,
      "section-readiness": readinessPending > 0,
      "section-draft": pendingMap.draft > 0,
    };
    Object.entries(sectionPending).forEach(([id, has]) => {
      const card = document.getElementById(id);
      if (card) card.classList.toggle("has-pending", has);
    });
  }

  function renderWorkflow(wf) {
    const status = wf.status || "未启动";
    if (els.wfStatus) {
      els.wfStatus.textContent = humanizeWorkflowStatus(status);
      els.wfStatus.className = "metric-value";
      if (status && status !== "未启动") els.wfStatus.classList.add("wf-" + status.toLowerCase());
    }
    if (els.wfNode) els.wfNode.textContent = wf.current_node || "—";
    if (els.wfBlocking) els.wfBlocking.textContent = wf.blocking_reason || "";
    root.dataset.currentNode = wf.current_node || "";
    renderWorkflowStepper(wf, workspace && workspace.node_labels);
    renderHeaderActions(wf);
  }

  function renderCaseStatus(status) {
    const pill = document.getElementById("case-status-pill");
    if (!pill) return;
    const s = String(status || "OPEN").toUpperCase();
    pill.textContent = s;
    pill.className = "status-pill " + s;
  }

  function renderReadiness(r) {
    if (!els.readinessStatus) return;
    if (!r) {
      els.readinessStatus.textContent = "—";
      els.readinessStatus.classList.remove("ready", "not-ready");
      if (els.readinessBlockers) els.readinessBlockers.textContent = "";
      return;
    }
    const display = r.display_status || (r.status === "READY" ? "已具备" : "尚未具备");
    if (els.readinessStatus) {
      els.readinessStatus.textContent = display;
      els.readinessStatus.classList.toggle("ready", r.status === "READY");
      els.readinessStatus.classList.toggle("not-ready", r.status !== "READY");
    }
    if (els.summaryReadiness) {
      els.summaryReadiness.textContent = display;
      els.summaryReadiness.classList.toggle("ready", r.status === "READY");
      els.summaryReadiness.classList.toggle("not-ready", r.status !== "READY");
    }
    if (!els.readinessBlockers) return;
    if (r.status === "READY") {
      els.readinessBlockers.textContent = "";
      return;
    }
    const blockers = (r.blocking_issues || []).map((i) => i.message || i.code).filter(Boolean);
    if (!blockers.length) {
      els.readinessBlockers.textContent = "尚未具备生成起诉状条件";
      return;
    }
    const items = blockers.slice(0, 8);
    const listHtml = items.map((m) => `<li>${escapeHtml(m)}</li>`).join("");
    if (items.length === 1 && items[0].length > 120) {
      els.readinessBlockers.innerHTML =
        `<div>尚未具备生成起诉状条件</div>
         <details open>
           <summary>查看阻塞原因</summary>
           <ul>${listHtml}</ul>
         </details>`;
    } else {
      els.readinessBlockers.innerHTML =
        "<div>尚未具备生成起诉状条件</div><div>还需解决：</div><ul>" + listHtml + "</ul>";
    }
  }

  function renderAi(ai) {
    if (!els.aiMode) return;
    els.aiMode.textContent = (ai && ai.ai_mode) || "Deterministic";
    if (els.aiModel) {
      els.aiModel.textContent = ai && ai.model ? " · " + ai.model : "";
    }
  }

  function renderAll(data) {
    workspace = data;
    renderCaseStatus(data.case && data.case.status);
    renderAi(data.ai || {});
    renderWorkflow(data.workflow || {});
    renderReadiness(data.pleading_readiness);
    renderWorkProduct(data.work_product);
    renderMaterialPool(data);
    renderParties(data.parties || []);
    renderEvidence(data.evidence || []);
    renderFacts(data.facts || []);
    renderClaim(data.claim_direction);
    renderDraft(data.draft);
    renderChat(data.conversation || []);
    renderNavBadges(data);
  }

  async function executeWorkAction(actionType, target, sectionAnchor) {
    if (actionType === "NAVIGATE") {
      navigateToSection(target || sectionAnchor);
      return;
    }
    if (actionType === "VIEW_DRAFT") {
      openDraftModal(workspace && workspace.draft);
      return;
    }
    if (actionType === "VIEW_EVIDENCE" && workspace) {
      const ev = (workspace.evidence || []).find((e) => String(e.id) === String(target));
      if (ev) showEvidenceModal(ev);
      return;
    }
    if (root.dataset.busy === "1") return;
    root.dataset.busy = "1";
    els.chatError.hidden = true;
    const loading = document.getElementById("agent-loading");
    if (loading) loading.hidden = false;
    document.querySelectorAll("button").forEach((b) => {
      if (b instanceof HTMLButtonElement) b.disabled = true;
    });
    try {
      const res = await fetch(`/api/cases/${caseId}/actions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action_type: actionType,
          target: target || null,
          conversation_id: conversationId,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        els.chatError.hidden = false;
        const detail = data.detail || data.message || res.statusText;
        els.chatError.textContent =
          typeof detail === "string" ? detail : JSON.stringify(detail);
        return;
      }
      if (data.conversation_id) {
        conversationId = data.conversation_id;
        localStorage.setItem(storageKey, conversationId);
      }
      await refreshWorkspace();
      if (data.message) showToast(data.message, "ok");
    } catch (err) {
      els.chatError.hidden = false;
      els.chatError.textContent = "网络或服务异常，请稍后重试。";
    } finally {
      root.dataset.busy = "0";
      if (loading) loading.hidden = true;
      document.querySelectorAll("button").forEach((b) => {
        if (b instanceof HTMLButtonElement) b.disabled = false;
      });
    }
  }

  async function refreshWorkspace() {
    const res = await fetch(`/api/cases/${caseId}/workspace`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    renderAll(data);
    return data;
  }

  async function sendAgentMessage(message) {
    if (root.dataset.busy === "1") return;
    root.dataset.busy = "1";
    els.chatError.hidden = true;

    const optimistic = {
      role: "USER",
      content: message,
      created_at: new Date().toISOString(),
      _pending: true,
    };
    const prevMessages = (workspace && workspace.conversation) || [];
    renderChat([...prevMessages, optimistic]);
    const loading = document.getElementById("agent-loading");
    if (loading) {
      loading.hidden = false;
      const hint = loading.querySelector("[data-loading-text]");
      if (hint) {
        const lower = String(message || "");
        const node = (root.dataset.currentNode || "").toUpperCase();
        if (node.includes("N4") || lower.includes("分析")) {
          hint.textContent = "AI 正在分析案件……请勿重复提交。";
        } else if (
          node.includes("N8") ||
          lower.includes("生成") ||
          lower.includes("起诉状")
        ) {
          hint.textContent = "AI 正在起草起诉状……请勿重复提交。";
        } else if (lower.includes("继续") || lower.includes("开始")) {
          hint.textContent = "AI 正在处理案件……请勿重复提交。";
        } else {
          hint.textContent = "正在处理你的指令……请勿重复提交。";
        }
      }
    }
    document.querySelectorAll("button, #chat-form button").forEach((b) => {
      if (b instanceof HTMLButtonElement) b.disabled = true;
    });
    try {
      const body = { message, conversation_id: conversationId };
      const res = await fetch(`/cases/${caseId}/agent/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data.detail || data.message || res.statusText;
        els.chatError.hidden = false;
        els.chatError.textContent =
          typeof detail === "string" ? detail : JSON.stringify(detail);
        return;
      }
      if (data.conversation_id) {
        conversationId = data.conversation_id;
        localStorage.setItem(storageKey, conversationId);
      }
      const bits = [];
      if (data.current_node_label) bits.push(data.current_node_label);
      else if (data.current_node) bits.push(data.current_node);
      if (data.workflow_status === "WAITING_USER") bits.push("等待你确认");
      else if (data.workflow_status === "WAITING_RETRY") bits.push("可重试");
      else if (data.workflow_status === "SUCCEEDED") bits.push("已完成");
      if (data.blocking_reason) bits.push(data.blocking_reason);
      if (data.error_code) bits.push("需处理错误");
      if (els.agentMeta) {
        if (!bits.length) {
          els.agentMeta.hidden = true;
          els.agentMeta.innerHTML = "";
        } else {
          els.agentMeta.hidden = false;
          els.agentMeta.innerHTML = bits
            .map((b) => {
              let cls = "meta-chip";
              if (/等待|阻塞|错误|需处理/.test(b)) cls += " warn";
              else if (/完成|成功/.test(b)) cls += " ok";
              return `<span class="${cls}">${escapeHtml(b)}</span>`;
            })
            .join("");
        }
      }
      const diag = document.getElementById("agent-diag");
      if (diag) {
        diag.textContent = [
          data.intent || "",
          data.routing_status ? `routing=${data.routing_status}` : "",
          data.workflow_status || "",
          data.current_node || "",
          data.error_code || "",
          data.safety_result ? `safety=${data.safety_result}` : "",
          Array.isArray(data.missing_fields) && data.missing_fields.length
            ? `missing_fields=${JSON.stringify(data.missing_fields)}`
            : "",
          data.pending_action
            ? `pending=${JSON.stringify(data.pending_action)}`
            : "",
          data.recent_focus
            ? `focus=${JSON.stringify(data.recent_focus)}`
            : "",
        ]
          .filter(Boolean)
          .join(" · ");
      }
      await refreshWorkspace();
    } catch (err) {
      els.chatError.hidden = false;
      els.chatError.textContent = "网络或服务异常，请稍后重试。";
    } finally {
      root.dataset.busy = "0";
      if (loading) loading.hidden = true;
      document.querySelectorAll("button").forEach((b) => {
        if (b instanceof HTMLButtonElement) b.disabled = false;
      });
    }
  }

  const chatInput = document.getElementById("chat-input");
  if (chatInput) {
    chatInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        document.getElementById("chat-form").requestSubmit();
      }
    });
    chatInput.addEventListener("input", () => {
      chatInput.style.height = "auto";
      chatInput.style.height = Math.min(chatInput.scrollHeight, 128) + "px";
    });
  }

  document.getElementById("chat-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = document.getElementById("chat-input");
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    input.style.height = "auto";
    await sendAgentMessage(msg);
  });

  document.getElementById("quick-actions").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-msg]");
    if (!btn) return;
    btn.classList.add("clicked");
    setTimeout(() => btn.classList.remove("clicked"), 600);
    await sendAgentMessage(btn.dataset.msg);
  });

  if (els.btnNextAction) {
    els.btnNextAction.addEventListener("click", async () => {
      const actionType = els.btnNextAction.dataset.workAction;
      const target = els.btnNextAction.dataset.actionTarget || null;
      const section = els.btnNextAction.dataset.sectionAnchor || null;
      await executeWorkAction(actionType, target, section);
    });
  }

  root.addEventListener("click", async (ev) => {
    const expandBtn = ev.target.closest("[data-expand]");
    if (expandBtn) {
      const key = expandBtn.dataset.expand;
      const cell = root.querySelector(`[data-expand-key="${key}"]`);
      if (cell) {
        cell.classList.add("expanded");
        cell.textContent = cell.dataset.fullText || cell.textContent.replace(/…$/, "");
        expandBtn.remove();
      }
      return;
    }
    const navBtn = ev.target.closest("[data-navigate]");
    if (navBtn) {
      navigateToSection(navBtn.dataset.navigate);
      return;
    }
    const viewEvBtn = ev.target.closest("[data-view-evidence]");
    if (viewEvBtn && workspace) {
      const evId = viewEvBtn.dataset.viewEvidence;
      const item = (workspace.evidence || []).find((e) => String(e.id) === String(evId));
      if (item) showEvidenceModal(item);
      return;
    }
    const viewDraftBtn = ev.target.closest("[data-view-draft]");
    if (viewDraftBtn) {
      openDraftModal(workspace && workspace.draft);
      return;
    }
    const workBtn = ev.target.closest("[data-work-action]");
    if (workBtn) {
      await executeWorkAction(
        workBtn.dataset.workAction,
        workBtn.dataset.actionTarget || null
      );
      return;
    }
    const btn = ev.target.closest("[data-agent-msg]");
    if (!btn) return;
    await sendAgentMessage(btn.dataset.agentMsg);
  });

  const uploadForm = document.getElementById("upload-form");
  const uploadZone = uploadForm;
  const uploadZoneInner = uploadForm && uploadForm.querySelector(".upload-zone-inner");
  const uploadFileInput = document.getElementById("upload-file");
  if (uploadZoneInner && uploadFileInput) {
    uploadZoneInner.addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      uploadFileInput.click();
    });
    uploadFileInput.addEventListener("change", () => {
      if (uploadFileInput.files && uploadFileInput.files.length) {
        uploadForm.requestSubmit();
      }
    });
  }
  if (uploadZone) {
    ["dragenter", "dragover"].forEach((evt) => {
      uploadZone.addEventListener(evt, (ev) => {
        ev.preventDefault();
        uploadZone.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach((evt) => {
      uploadZone.addEventListener(evt, (ev) => {
        ev.preventDefault();
        uploadZone.classList.remove("dragover");
      });
    });
    uploadZone.addEventListener("drop", (ev) => {
      const fileInput = document.getElementById("upload-file");
      const files = ev.dataTransfer && ev.dataTransfer.files;
      if (fileInput && files && files.length) {
        fileInput.files = files;
        uploadForm.requestSubmit();
      }
    });
  }

  document.getElementById("upload-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    els.uploadError.hidden = true;
    if (els.uploadFeedback) {
      els.uploadFeedback.hidden = true;
      els.uploadFeedback.textContent = "";
    }
    const fileInput = document.getElementById("upload-file");
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      els.uploadError.hidden = false;
      els.uploadError.textContent = "请选择 PDF、DOCX 或 MD 文件。";
      return;
    }
    const scanHint = /\.pdf$/i.test(file.name)
      ? `正在解析「${file.name}」。扫描件可能需要 1–5 分钟，请稍候……`
      : `正在解析「${file.name}」……`;
    setParseLoading(true, scanHint);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`/api/cases/${caseId}/materials`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        els.uploadError.hidden = false;
        els.uploadError.textContent = data.detail || "上传失败";
        return;
      }
      const msg =
        data.message ||
        (data.success
          ? "文件已上传并读取成功，已进入案件材料。"
          : "文件已上传，但 AI 暂时无法读取。该文件尚未进入案件材料，也不会参与证据整理和案件分析。");
      if (data.usable || data.success) {
        if (els.uploadFeedback) {
          els.uploadFeedback.hidden = false;
          els.uploadFeedback.textContent = msg;
        }
        showToast(msg, "ok");
      } else {
        els.uploadError.hidden = false;
        const reason = humanizeExtractionError(data.extraction_error || "");
        els.uploadError.textContent = reason ? `${msg}\n原因：${reason}` : msg;
        showToast(reason || msg, "error");
      }
      fileInput.value = "";
      await refreshWorkspace();
    } catch (err) {
      els.uploadError.hidden = false;
      els.uploadError.textContent = "网络或服务异常，请稍后重试。";
    } finally {
      setParseLoading(false);
    }
  });

  root.addEventListener("click", async (ev) => {
    const voidBtn = ev.target.closest("[data-void-material]");
    if (voidBtn) {
      const mid = voidBtn.dataset.voidMaterial;
      const name = voidBtn.dataset.voidName || "该文件";
      const ok = await confirmAction(
        "作废材料",
        `确认作废「${name}」？\n将移入作废池，不再进入案件材料池。`,
        { okLabel: "作废", danger: true }
      );
      if (!ok) return;
      const res = await fetch(`/api/cases/${caseId}/materials/${mid}/void`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "律师在工作台作废材料" }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        els.uploadError.hidden = false;
        els.uploadError.textContent = data.detail || "作废失败";
        return;
      }
      await refreshWorkspace();
      showToast(`「${name}」已移入作废池`, "ok");
      return;
    }
    const reparseBtn = ev.target.closest("[data-reparse-material]");
    if (reparseBtn) {
      const mid = reparseBtn.dataset.reparseMaterial;
      const name = reparseBtn.dataset.reparseName || "该文件";
      reparseBtn.disabled = true;
      els.uploadError.hidden = true;
      if (els.uploadFeedback) {
        els.uploadFeedback.hidden = true;
        els.uploadFeedback.textContent = "";
      }
      setParseLoading(true, `正在重新解析「${name}」。扫描件可能需要较长时间，请稍候……`);
      try {
        const res = await fetch(`/api/cases/${caseId}/materials/${mid}/reparse`, {
          method: "POST",
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          els.uploadError.hidden = false;
          els.uploadError.textContent = data.detail || "重新解析失败";
          return;
        }
        if (data.usable) {
          if (els.uploadFeedback) {
            els.uploadFeedback.hidden = false;
            els.uploadFeedback.textContent = data.message || "重新解析成功，已进入案件材料。";
          }
          els.uploadError.hidden = true;
        } else {
          els.uploadError.hidden = false;
          els.uploadError.textContent =
            data.message ||
            humanizeExtractionError(data.extraction_error) ||
            "重新解析仍失败";
        }
        await refreshWorkspace();
      } catch (err) {
        els.uploadError.hidden = false;
        els.uploadError.textContent = "网络或服务异常，请稍后重试。";
      } finally {
        setParseLoading(false);
        reparseBtn.disabled = false;
      }
    }
  });

  const partyForm = document.getElementById("party-form");
  const partyToggle = document.getElementById("btn-toggle-party-form");
  const partyCancel = document.getElementById("btn-cancel-party-form");
  const partyError = document.getElementById("party-form-error");

  function setPartyFormOpen(open) {
    if (!partyForm) return;
    partyForm.hidden = !open;
    if (partyToggle) {
      partyToggle.textContent = open ? "收起表单" : "+ 新增当事人";
    }
    if (partyError) {
      partyError.hidden = true;
      partyError.textContent = "";
    }
  }

  if (partyToggle) {
    partyToggle.addEventListener("click", () => setPartyFormOpen(partyForm.hidden));
  }
  if (partyCancel) {
    partyCancel.addEventListener("click", () => setPartyFormOpen(false));
  }
  if (partyForm) {
    partyForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (partyError) {
        partyError.hidden = true;
        partyError.textContent = "";
      }
      const role = document.getElementById("party-role").value;
      const name = document.getElementById("party-name").value;
      const body = {
        role,
        name,
        address: document.getElementById("party-address").value || null,
        legal_representative: document.getElementById("party-legal-rep").value || null,
        credit_code: document.getElementById("party-credit-code").value || null,
        contact: document.getElementById("party-contact").value || null,
      };
      const res = await fetch(`/api/cases/${caseId}/parties`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (partyError) {
          partyError.hidden = false;
          partyError.textContent =
            typeof data.detail === "string" ? data.detail : "新增当事人失败";
        }
        return;
      }
      partyForm.reset();
      setPartyFormOpen(false);
      await refreshWorkspace();
      showToast("当事人候选已保存", "ok");
    });
  }

  if (els.btnVoidPoolAdd) {
    els.btnVoidPoolAdd.addEventListener("click", async () => {
      const pendingN =
        (workspace &&
          workspace.material_pool &&
          workspace.material_pool.pending_count) ||
        (workspace && workspace.pending_materials && workspace.pending_materials.length) ||
        0;
      if (!pendingN) {
        showToast("当前没有待处理文件可移入作废池。", "info");
        return;
      }
      const okAdd = await confirmAction(
        "批量移入作废池",
        `确认将 ${pendingN} 份待处理文件全部移入作废池？\n移入后不再参与案件分析。`,
        { okLabel: "移入", danger: true }
      );
      if (!okAdd) return;
      const res = await fetch(`/api/cases/${caseId}/void-pool/add-pending`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "律师将待处理文件批量移入作废池" }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        els.uploadError.hidden = false;
        els.uploadError.textContent = data.detail || "移入作废池失败";
        return;
      }
      await refreshWorkspace();
    });
  }

  if (els.btnVoidPoolClear) {
    els.btnVoidPoolClear.addEventListener("click", async () => {
      const voidN =
        (workspace && workspace.material_pool && workspace.material_pool.void_count) ||
        (workspace && workspace.void_materials && workspace.void_materials.length) ||
        0;
      if (!voidN) {
        showToast("作废池已为空。", "info");
        return;
      }
      const okClear = await confirmAction(
        "清空作废池",
        `确认清空作废池中的 ${voidN} 项？\n列表将清空；磁盘文件仍保留，不会恢复到材料池。`,
        { okLabel: "清空", danger: true }
      );
      if (!okClear) return;
      const res = await fetch(`/api/cases/${caseId}/void-pool/clear`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        els.uploadError.hidden = false;
        els.uploadError.textContent = data.detail || "清空作废池失败";
        return;
      }
      await refreshWorkspace();
    });
  }

  document.querySelectorAll(".collapse-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.collapse;
      const card = id && document.getElementById(id);
      if (!card) return;
      card.classList.toggle("collapsed");
    });
  });

  const btnRefresh = document.getElementById("btn-refresh");
  if (btnRefresh) {
    btnRefresh.addEventListener("click", async () => {
      btnRefresh.disabled = true;
      btnRefresh.textContent = "刷新中…";
      try {
        await refreshWorkspace();
        showToast("工作台已刷新", "ok");
      } catch (err) {
        showToast("刷新失败，请稍后重试", "error");
      } finally {
        btnRefresh.disabled = false;
        btnRefresh.textContent = "↻ 刷新";
      }
    });
  }

  const btnCopyDraft = document.getElementById("btn-copy-draft");
  if (btnCopyDraft) {
    btnCopyDraft.addEventListener("click", async () => {
      const pane = document.querySelector(".draft-pane:not([hidden])");
      const text = pane ? pane.textContent : "";
      if (!text) {
        showToast("当前页无内容可复制", "info");
        return;
      }
      try {
        await navigator.clipboard.writeText(text);
        showToast("已复制到剪贴板", "ok");
      } catch (err) {
        showToast("复制失败", "error");
      }
    });
  }

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "/" && !ev.ctrlKey && !ev.metaKey) {
      const tag = (document.activeElement && document.activeElement.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      ev.preventDefault();
      const input = document.getElementById("chat-input");
      if (input) {
        input.focus();
        const chat = document.querySelector(".chat");
        if (chat) chat.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }
    if (ev.key === "Escape") {
      if (els.confirmDialog && els.confirmDialog.open) els.confirmDialog.close("cancel");
      if (els.draftModal && els.draftModal.open) els.draftModal.close("cancel");
    }
  });

  const fabTop = document.getElementById("fab-top");
  const fabChat = document.getElementById("fab-chat");
  if (fabTop) {
    window.addEventListener("scroll", () => {
      fabTop.hidden = window.scrollY < 400;
    }, { passive: true });
    fabTop.addEventListener("click", () => {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }
  if (fabChat) {
    fabChat.addEventListener("click", () => {
      const chat = document.querySelector(".chat");
      if (chat) chat.scrollIntoView({ behavior: "smooth", block: "start" });
      const input = document.getElementById("chat-input");
      if (input) setTimeout(() => input.focus(), 400);
    });
  }

  document.querySelectorAll(".draft-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".draft-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const name = tab.dataset.draftTab;
      document.querySelectorAll(".draft-pane").forEach((pane) => {
        const show = pane.dataset.draftPane === name;
        pane.hidden = !show;
        pane.classList.toggle("active", show);
      });
    });
  });

  document.querySelectorAll(".section-nav-link").forEach((link) => {
    link.addEventListener("click", (ev) => {
      ev.preventDefault();
      const target = document.querySelector(link.getAttribute("href"));
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  const sectionIds = [
    "action-center",
    "materials",
    "evidence",
    "facts",
    "parties",
    "claim",
    "readiness",
    "draft",
    "timeline",
  ];
  const navLinks = [...document.querySelectorAll(".section-nav-link")];
  if (navLinks.length && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const id = entry.target.id.replace("section-", "");
          navLinks.forEach((l) => {
            l.classList.toggle("active", l.getAttribute("href") === `#section-${id}`);
          });
        });
      },
      { rootMargin: "-20% 0px -65% 0px", threshold: 0 }
    );
    sectionIds.forEach((id) => {
      const el = document.getElementById(`section-${id}`);
      if (el) observer.observe(el);
    });
  }

  const savedSection = localStorage.getItem(ACTIVE_SECTION_KEY);
  if (savedSection && document.getElementById(`section-${savedSection}`)) {
    setTimeout(() => navigateToSection(savedSection), 300);
  }

  if (workspace) renderAll(workspace);
})();

