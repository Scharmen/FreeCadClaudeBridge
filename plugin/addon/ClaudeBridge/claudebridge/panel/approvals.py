"""Claude's permission requests and questions: cards with buttons, the approval bar, Auto mode."""

from __future__ import annotations

import html
from typing import Any

from PySide import QtCore, QtWidgets

from .common import (
    BUTTON_COLORS,
    _always_label,
    _buttons,
    _params,
)
from .stream import (
    answered_input,
    auto_answers,
    questions,
    short_tool_name,
)


class ApprovalsMixin:
    def _on_auto_toggled(self, on: bool) -> None:
        _params().SetBool("AutoApprove", on)
        if on:
            self._note("⚡ Auto is on: Claude's actions are allowed, its questions answered with the recommended "
                       "option and its plans accepted, without asking you.", error=True)
            for rid in list(self.perms):
                self._auto_answer(self.perms.pop(rid))
            self._update_approval_bar()
        else:
            self._note("Auto is off: Claude asks again before acting.")

    def _auto_answer(self, ev: dict[str, Any]) -> None:
        if not self.session:
            return
        inp = auto_answers(ev["tool"], ev.get("input") or {})
        self.session.answer_permission(ev["request_id"], "allow", inp)
        picked = "; ".join(f"{q} → {a}" for q, a in (inp.get("answers") or {}).items())
        self._note(f"⚡ Auto-answered: {picked}" if picked else f"⚡ Auto-allowed: {ev.get('summary', ev['tool'])}")

    def _permission_card(self, ev: dict[str, Any]) -> None:
        rid, tool, inp = ev["request_id"], ev["tool"], ev.get("input") or {}
        if self.auto_btn.isChecked() and self.session:
            self._auto_answer(ev)
            return
        if tool in self.session_always:
            self.session.answer_permission(rid, "allow", inp)
            self._note(f"Allowed (this session): {ev.get('summary', tool)}")
            return
        self.perms[rid] = ev
        qs = questions(tool, inp)
        if qs:                                          # Claude asks the user something: options as buttons
            ev["questions"], ev["answers"] = qs, {}
            parts = ["<table border='1' cellspacing='0' cellpadding='6' width='100%'><tr><td>"
                     "<b>❓ Claude asks</b>"]
            for qi, q in enumerate(qs):
                parts.append(f"<br><b>{html.escape(q['header'] or '')}</b> {html.escape(q['question'])}")
                descs = q.get("descriptions") or []
                parts.append(_buttons([(opt, f"perm:ans:{rid}:{qi}:{oi}", "option")
                                       for oi, opt in enumerate(q["options"])],
                                      tip={f"perm:ans:{rid}:{qi}:{oi}": d for oi, d in enumerate(descs) if d}))
            parts.append(_buttons([("Skip", f"perm:deny:{rid}", "deny")]) + "</td></tr></table>")
            self._append("".join(parts))
            self._update_approval_bar()
            QtWidgets.QApplication.alert(self)
            return

        def mono(text: str) -> str:   # wraps inside the narrow panel, unlike <pre>
            return ("<div style='font-family:monospace; white-space:pre-wrap'>"
                    + html.escape(text).replace("\n", "<br>") + "</div>")

        detail = mono(ev.get("summary", "").split(": ", 1)[-1])
        if inp.get("command"):
            detail = mono(str(inp["command"])[:1500])
            if inp.get("description"):
                detail = f"<i>{html.escape(str(inp['description']))}</i>" + detail
        elif "old_string" in inp or "new_string" in inp:
            old = "\n".join("− " + ln for ln in str(inp.get("old_string", "")).splitlines()[:8])
            new = "\n".join("+ " + ln for ln in str(inp.get("new_string", "")).splitlines()[:8])
            detail = f"<i>{html.escape(str(inp.get('file_path', '')))}</i>" + mono(old + "\n" + new)
        elif inp.get("content"):
            detail = (f"<i>{html.escape(str(inp.get('file_path', '')))}</i>"
                      + mono("\n".join(str(inp["content"]).splitlines()[:10])))
        elif inp.get("plan"):
            detail = mono(str(inp["plan"])[:2000])
        always = _always_label(ev.get("suggestions") or [], short_tool_name(tool))
        ev["always_label"] = always
        short = always if len(always) <= 36 else always[:33].rstrip() + "…"
        title = ("📋 Claude has a plan" if tool == "ExitPlanMode"
                 else f"🔐 Claude wants to use {html.escape(short_tool_name(tool))}")
        first = "Accept plan" if tool == "ExitPlanMode" else "Allow once"
        self._append(
            "<table border='1' cellspacing='0' cellpadding='6' width='100%'><tr><td>"
            f"<b>{title}</b><br>{detail}"
            + _buttons([(first, f"perm:allow:{rid}", "allow"), (short, f"perm:always:{rid}", "always"),
                        ("Deny", f"perm:deny:{rid}", "deny")], tip={f"perm:always:{rid}": always})
            + "</td></tr></table>")
        self.status.setText("Waiting for your approval…")
        self._update_approval_bar()
        QtWidgets.QApplication.alert(self)

    def _update_approval_bar(self) -> None:
        """Real buttons above the input for the oldest open request, so it never scrolls out of sight."""
        ev = next(iter(self.perms.values()), None)
        if ev is None:
            self.approve_bar.setVisible(False)
            return
        is_q = bool(ev.get("questions"))
        more = f"  (+{len(self.perms) - 1} more)" if len(self.perms) > 1 else ""
        while self.option_lay.count():
            w = self.option_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        if is_q:
            qi, q = next(((i, q) for i, q in enumerate(ev["questions"]) if q["question"] not in ev["answers"]),
                         (0, ev["questions"][0]))
            text = f"❓ {q['question']}"
            for oi, opt in enumerate(q["options"]):
                btn = QtWidgets.QPushButton(opt)
                descs = q.get("descriptions") or []
                btn.setToolTip(descs[oi] if oi < len(descs) and descs[oi] else "Answer with this option")
                btn.setStyleSheet(f"QPushButton {{ background-color: {BUTTON_COLORS['option']}; color: white; "
                                  "border-radius: 4px; padding: 3px 8px; font-weight: bold; }")
                btn.clicked.connect(lambda _=False, r=ev["request_id"], a=qi, b=oi: self._answer_question(r, a, b))
                self.option_lay.addWidget(btn)
        else:
            text = f"🔐 {ev.get('summary', ev['tool'])}"
        metrics = self.approve_label.fontMetrics()
        self.approve_label.setText(metrics.elidedText(text + more, QtCore.Qt.ElideMiddle,
                                                      max(120, self.approve_label.width())))
        self.approve_label.setToolTip(text)
        self.approve_btns["allow"].setVisible(not is_q)
        self.approve_btns["allow"].setText("Accept plan" if ev["tool"] == "ExitPlanMode" else "Allow once")
        self.approve_btns["always"].setVisible(not is_q)
        self.approve_btns["always"].setToolTip(ev.get("always_label", ""))
        self.approve_btns["deny"].setText("Skip" if is_q else "Deny")
        self.approve_btns["allow"].setToolTip("Accept the plan; Claude starts building" if ev["tool"] == "ExitPlanMode"
                                              else "Allow this action once")
        self.approve_btns["deny"].setToolTip("Skip this question; Claude goes on without your answer" if is_q
                                             else "Refuse this action; Claude is told and tries something else")
        self.approve_bar.setVisible(True)

    def _bar_answer(self, decision: str) -> None:
        rid = next(iter(self.perms), None)
        if rid:
            self._answer_permission(rid, decision)

    def _answer_question(self, rid: str, qi: int, oi: int) -> None:
        ev = self.perms.get(rid)
        if not ev or not self.session or qi >= len(ev.get("questions", [])):
            self._note("This question is no longer open.")
            return
        q = ev["questions"][qi]
        if oi >= len(q["options"]):
            return
        ev["answers"][q["question"]] = q["options"][oi]
        self._note(f"{q['header'] or q['question']}: {q['options'][oi]}")
        if len(ev["answers"]) == len(ev["questions"]):
            self.perms.pop(rid, None)
            self.session.answer_permission(rid, "allow", answered_input(ev.get("input") or {}, ev["answers"]))
            self.status.setText("Claude is working…")
        self._update_approval_bar()

    def _answer_permission(self, rid: str, decision: str) -> None:
        ev = self.perms.pop(rid, None)
        if not ev or not self.session or decision not in ("allow", "always", "deny"):
            self._note("This request is no longer open.")
            return
        # no modal dialog here: a dialog opened from a Python slot holds the GIL and stalls the bridge
        message = "The user declined this action; wait for their next message." if decision == "deny" else ""
        if decision == "always" and not ev.get("suggestions"):
            self.session_always.add(ev["tool"])
        self.session.answer_permission(rid, decision, ev.get("input") or {}, ev.get("suggestions"),
                                       message or "The user declined this action.")
        self._update_approval_bar()
        verdict = {"allow": "✓ Allowed once", "always": "✓ Allowed", "deny": "✗ Denied"}[decision]
        self._note(f"{verdict}: {ev.get('summary', ev['tool'])}"
                   + (". Type what Claude should do instead." if decision == "deny" else ""))
        if decision == "deny":
            self.input.setFocus()
        self.status.setText("Claude is working…")
