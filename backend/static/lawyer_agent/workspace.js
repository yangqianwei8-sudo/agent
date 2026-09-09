(() => {
  const root = document.querySelector(".workspace");
  if (!root) return;
  const caseId = root.dataset.caseId;
  const storageKey = `lca_conversation_${caseId}`;
  let conversationId = localStorage.getItem(storageKey) || null;
  let workspace = window.__WORKSPACE_BOOT__ || null;

  const els = {
    materialsBody: document.querySelector("#materials-table tbody"),
    parties: document.getElementById("parties-panel"),
    evidence: document.getElementById("evidence-panel"),
    facts: document.getElementById("facts-panel"),
    claim: document.getElementById("claim-panel"),
    draft: document.getElementById("draft-panel"),
    chatLog: document.getElementById("chat-log"),
    agentMeta: document.getElementById("agent-meta"),
    chatError: document.getElementById("chat-error"),
    uploadError: document.getElementById("upload-error"),
    wfStatus: document.getElementById("wf-status"),
    wfNode: document.getElementById("wf-node"),
    wfPending: document.getElementById("wf-pending"),
    wfBlocking: document.getElementById("wf-blocking"),
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

  function renderMaterials(list) {
    els.materialsBody.innerHTML = (list || [])
      .map((m) => {
        const status = m.extraction_status || m.parse_status || "—";
        const err = m.extraction_error
          ? `<div class="muted">${escapeHtml(m.extraction_error)}</div>`
          : "";
        return `<tr>
          <td>${escapeHtml(m.filename)}</td>
          <td>${escapeHtml(m.mime)}</td>
          <td>${escapeHtml(m.created_at || "—")}</td>
          <td>${badge(status)}${err}</td>
          <td>${escapeHtml(m.extraction_method || "—")}</td>
        </tr>`;
      })
      .join("");
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

  function renderChat(messages) {
    els.chatLog.innerHTML = (messages || [])
      .map(
        (m) => `<div class="msg ${m.role}">
          <div class="role">${escapeHtml(m.role)} · ${escapeHtml(m.created_at || "")}</div>
          <div>${escapeHtml(m.content)}</div>
        </div>`
      )
      .join("");
    els.chatLog.scrollTop = els.chatLog.scrollHeight;
  }

  function renderWorkflow(wf) {
    els.wfStatus.textContent = wf.status || "未启动";
    els.wfNode.textContent = wf.current_node_label || "—";
    els.wfPending.textContent = wf.pending_count || 0;
    els.wfBlocking.textContent = wf.blocking_reason || "";
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
    renderMaterials(data.materials || []);
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
        if (lower.includes("继续") || lower.includes("开始")) {
          hint.textContent = "AI 正在处理案件……请勿重复提交。";
        } else if (lower.includes("生成") || lower.includes("起诉状")) {
          hint.textContent = "AI 正在起草起诉状……请勿重复提交。";
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
          data.workflow_status || "",
          data.current_node || "",
          data.error_code || "",
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
    const fileInput = document.getElementById("upload-file");
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      els.uploadError.hidden = false;
      els.uploadError.textContent = "请选择 PDF 或 DOCX 文件。";
      return;
    }
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/cases/${caseId}/materials`, { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      els.uploadError.hidden = false;
      els.uploadError.textContent = data.detail || "上传失败";
      return;
    }
    if (!data.success) {
      els.uploadError.hidden = false;
      els.uploadError.textContent =
        "文件已登记，但解析未成功：" + (data.extraction_error || data.extraction_status);
    }
    fileInput.value = "";
    await refreshWorkspace();
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

  if (workspace) renderAll(workspace);
})();
