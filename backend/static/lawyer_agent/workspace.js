(() => {
  const root = document.querySelector(".workspace");
  if (!root) return;
  const caseId = root.dataset.caseId;
  const storageKey = `lca_conversation_${caseId}`;
  let conversationId = localStorage.getItem(storageKey) || null;
  let workspace = window.__WORKSPACE_BOOT__ || null;

  const els = {
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
  };

  function badge(text) {
    return `<span class="badge ${text || ""}">${text || "—"}</span>`;
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
        `<tr><td colspan="6" class="muted">暂无已成功读取的案件材料。</td></tr>`;
      return;
    }
    els.materialsBody.innerHTML = list
      .map((m) => {
        const pages = m.page_count != null ? m.page_count : "—";
        return `<tr>
          <td>${escapeHtml(m.filename)}</td>
          <td>${escapeHtml(formatMaterialType(m))}</td>
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
    if (!list || !list.length) {
      els.pendingPanel.innerHTML = `<p class="muted">没有待处理文件。</p>`;
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
    if (!list || !list.length) {
      els.voidPanel.innerHTML = `<p class="muted">作废池为空。</p>`;
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

  function renderParties(list) {
    if (!list || !list.length) {
      els.parties.innerHTML = `<p class="muted">暂无当事人。请点击上方「新增当事人」录入原告/被告候选。</p>`;
      return;
    }
    els.parties.innerHTML = `<table class="table"><thead><tr><th>#</th><th>角色</th><th>名称</th><th>状态</th><th></th></tr></thead><tbody>
      ${list
        .map(
          (p) => `<tr>
          <td>${p.display_index}</td>
          <td>${escapeHtml(p.role_label || p.role)}</td>
          <td>${escapeHtml(p.name)}</td>
          <td>${badge(p.status_label || p.layer)}</td>
          <td class="row-actions">
            ${
              p.layer === "CANDIDATE"
                ? `<button class="btn small" data-agent-msg="确认当事人${p.display_index}">确认</button>
                   <button class="btn small" data-agent-msg="拒绝当事人${p.display_index}">拒绝</button>`
                : ""
            }
          </td>
        </tr>`
        )
        .join("")}
    </tbody></table>`;
  }

  function renderEvidence(list) {
    if (!list || !list.length) {
      els.evidence.innerHTML = `<p class="muted">暂无证据。可先「开始处理」并「继续」到整理证据。</p>`;
      return;
    }
    els.evidence.innerHTML = `<table class="table"><thead><tr><th>编号</th><th>标题</th><th>摘要</th><th>状态</th><th>v</th><th></th></tr></thead><tbody>
      ${list
        .map(
          (e) => `<tr>
          <td>${escapeHtml(e.number)}</td>
          <td>${escapeHtml(e.title)}</td>
          <td>${escapeHtml((e.summary || "").slice(0, 80))}</td>
          <td>${badge(e.acceptance)}</td>
          <td>${e.version}</td>
          <td class="row-actions">
            ${
              e.acceptance === "PENDING"
                ? `<button class="btn small" data-agent-msg="接受证据${e.number}">接受</button>
                   <button class="btn small" data-agent-msg="排除证据${e.number}">排除</button>`
                : ""
            }
          </td>
        </tr>`
        )
        .join("")}
    </tbody></table>`;
  }

  function renderFacts(list) {
    if (!list || !list.length) {
      els.facts.innerHTML = `<p class="muted">暂无事实。</p>`;
      return;
    }
    els.facts.innerHTML = `<table class="table"><thead><tr><th>#</th><th>陈述</th><th>状态</th><th>v</th><th>stale</th><th></th></tr></thead><tbody>
      ${list
        .map(
          (f) => `<tr>
          <td>${f.display_index}</td>
          <td>${escapeHtml(f.statement)}</td>
          <td>${badge(f.status)}</td>
          <td>${f.version}</td>
          <td>${f.stale ? "是" : "否"}</td>
          <td class="row-actions">
            ${
              f.status === "CANDIDATE"
                ? `<button class="btn small" data-agent-msg="确认事实${f.display_index}">确认</button>
                   <button class="btn small" data-agent-msg="拒绝事实${f.display_index}">拒绝</button>`
                : ""
            }
          </td>
        </tr>`
        )
        .join("")}
    </tbody></table>`;
  }

  function renderClaim(c) {
    if (!c) {
      els.claim.innerHTML = `<p class="muted">暂无诉讼请求建议。</p>`;
      return;
    }
    els.claim.innerHTML = `
      <p>${badge(c.status)} v${c.version} ${c.stale ? "(stale)" : ""}</p>
      <p><strong>策略</strong>：${escapeHtml(c.overall_strategy || "—")}</p>
      <p><strong>类型</strong>：${escapeHtml(c.claim_type || "—")}</p>
      <p><strong>描述</strong>：${escapeHtml(c.description || "—")}</p>
      <p><strong>金额</strong>：${escapeHtml(c.amount ?? "—")} ${escapeHtml(c.currency || "")}</p>
      <p><strong>计算基础</strong>：${escapeHtml(c.calculation_basis || "—")}</p>
      ${
        c.status === "CANDIDATE"
          ? `<button class="btn small" data-agent-msg="确认诉讼请求1">确认诉讼请求</button>`
          : ""
      }
    `;
  }

  function renderDraft(d) {
    if (!d) {
      els.draft.innerHTML = `<p class="muted">暂无起诉状草稿。</p>`;
      return;
    }
    els.draft.innerHTML = `
      <p>${badge(d.status)} v${d.version} ${d.stale_reason ? "stale: " + escapeHtml(d.stale_reason) : ""}</p>
      <div class="row-actions">
        <button class="btn small" id="btn-view-draft">查看全文</button>
        ${
          d.status === "DRAFT" || d.status === "IN_REVIEW"
            ? `<button class="btn small primary" data-agent-msg="批准这份起诉状">批准这份起诉状</button>`
            : ""
        }
        <button class="btn small" data-agent-msg="生成起诉状">生成/重新生成</button>
      </div>
    `;
    const viewBtn = document.getElementById("btn-view-draft");
    if (viewBtn) {
      viewBtn.onclick = () => {
        els.draftBody.textContent = JSON.stringify(d.body_structured_json || {}, null, 2);
        els.draftModal.showModal();
      };
    }
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
    els.chatLog.innerHTML = (messages || [])
      .map((m) => {
        const roleLabel = m.role === "USER" ? "律师" : m.role === "AGENT" ? "助手" : m.role;
        return `<div class="msg ${m.role}">
          <div class="role">${escapeHtml(roleLabel)} · ${escapeHtml(formatWhen(m.created_at))}</div>
          <div class="msg-body">${renderMarkdownLite(m.content)}</div>
        </div>`;
      })
      .join("");
    els.chatLog.scrollTop = els.chatLog.scrollHeight;
  }

  function renderWorkflow(wf) {
    els.wfStatus.textContent = wf.status || "未启动";
    els.wfNode.textContent = wf.current_node_label || "—";
    els.wfPending.textContent = wf.pending_count || 0;
    els.wfBlocking.textContent = wf.blocking_reason || "";
    root.dataset.currentNode = wf.current_node || "";
  }

  function renderReadiness(r) {
    if (!els.readinessStatus) return;
    if (!r) {
      els.readinessStatus.textContent = "—";
      if (els.readinessBlockers) els.readinessBlockers.textContent = "";
      return;
    }
    const display = r.display_status || (r.status === "READY" ? "已具备" : "尚未具备");
    els.readinessStatus.textContent = display;
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
    els.readinessBlockers.innerHTML =
      "<div>尚未具备生成起诉状条件</div><div>还需解决：</div><ul>" +
      blockers
        .slice(0, 8)
        .map((m) => `<li>${escapeHtml(m)}</li>`)
        .join("") +
      "</ul>";
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
    renderAi(data.ai || {});
    renderWorkflow(data.workflow || {});
    renderReadiness(data.pleading_readiness);
    renderMaterialPool(data);
    renderParties(data.parties || []);
    renderEvidence(data.evidence || []);
    renderFacts(data.facts || []);
    renderClaim(data.claim_direction);
    renderDraft(data.draft);
    renderChat(data.conversation || []);
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
      els.agentMeta.textContent = bits.join(" · ");
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

  document.getElementById("chat-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = document.getElementById("chat-input");
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    await sendAgentMessage(msg);
  });

  document.getElementById("quick-actions").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-msg]");
    if (!btn) return;
    await sendAgentMessage(btn.dataset.msg);
  });

  root.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-agent-msg]");
    if (!btn) return;
    await sendAgentMessage(btn.dataset.agentMsg);
  });

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
      } else {
        els.uploadError.hidden = false;
        const reason = humanizeExtractionError(data.extraction_error || "");
        els.uploadError.textContent = reason ? `${msg}\n原因：${reason}` : msg;
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
      if (!window.confirm(`确认作废「${name}」？将移入作废池，不再进入案件材料池。`)) {
        return;
      }
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
        window.alert("当前没有待处理文件可移入作废池。");
        return;
      }
      if (
        !window.confirm(
          `确认将 ${pendingN} 份待处理文件全部移入作废池？移入后不再参与案件分析。`
        )
      ) {
        return;
      }
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
        window.alert("作废池已为空。");
        return;
      }
      if (
        !window.confirm(
          `确认清空作废池中的 ${voidN} 项？列表将清空；磁盘文件仍保留，不会恢复到材料池。`
        )
      ) {
        return;
      }
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

  if (workspace) renderAll(workspace);
})();
