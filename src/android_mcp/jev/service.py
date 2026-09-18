"""Decision layer backed by TypeSafe's System One model (Jev).

Code owns the workflow: the device screen is captured as state, Jev returns
typed judgments (Choice/Noul) over options the code defines, and the tools
in __main__ act on those answers. Jev never generates text — it picks among
candidates the code supplied — so every call is one fast parallel request.
"""
import os
from typing import Optional, TYPE_CHECKING

from android_mcp.jev.config import (
    DEFAULT_MAX_ELEMENTS,
    DEFAULT_MODEL,
    ENV_API_KEY,
    ENV_MAX_ELEMENTS,
    ENV_MODEL,
    GOAL_DONE_THRESHOLD,
    PRESENCE_THRESHOLD,
    VERDICT_NO,
    VERDICT_YES,
)

if TYPE_CHECKING:
    from android_mcp.mobile.service import Mobile
    from android_mcp.tree.views import ElementNode

NO_MATCH = "none"

# Actions Jev can choose in decide_step. Code maps each answer to execution.
ACTIONS = {
    "tap_element": "Tap one of the interactive elements listed in `screen`",
    "scroll_down": "Scroll down to reveal more content below",
    "scroll_up": "Scroll up to reveal earlier content above",
    "go_back": "Press the Android back button",
    "go_home": "Press the Android home button to return to the launcher",
    "press_enter": "Press the keyboard Enter/Go key to submit typed text or a URL",
    "wait": "Wait briefly for the screen to load or change",
    "done": "The goal is achieved or no further action is needed",
    "give_up": "The goal cannot be advanced from this screen",
}

TYPE_TEXT_ACTION = (
    "Type `text_to_type` into the field chosen by `type_target`. Code taps the "
    "field first to focus it, then enters the text. Only when the target does "
    "not already contain that text (no element has typed=true)"
)


class JevNotConfigured(RuntimeError):
    pass


class Jev:
    def __init__(self, mobile: "Mobile"):
        self.mobile = mobile
        self._client = None
        self._typed: set = set()
        self._typed_pkg: Optional[str] = None
        env_max = os.getenv(ENV_MAX_ELEMENTS, "").strip()
        try:
            self._max_elements = int(env_max) if env_max else DEFAULT_MAX_ELEMENTS
        except ValueError:
            self._max_elements = DEFAULT_MAX_ELEMENTS

    @property
    def is_configured(self) -> bool:
        return bool(os.getenv(ENV_API_KEY, "").strip())

    @property
    def sdk_available(self) -> bool:
        try:
            import typesafe_sdk  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def status(self) -> dict:
        return {
            "configured": self.is_configured,
            "sdk_installed": self.sdk_available,
            "model": os.getenv(ENV_MODEL, "").strip() or f"{DEFAULT_MODEL} (default)",
            "max_elements": self._max_elements,
        }

    @property
    def client(self):
        if not self.sdk_available:
            raise JevNotConfigured(
                "typesafe-sdk is not installed. Add it with `uv add typesafe-sdk`."
            )
        if not self.is_configured:
            raise JevNotConfigured(
                f"{ENV_API_KEY} is not set. Get a key at https://console.typesafe.ai/settings/keys "
                "and add it to the MCP server's environment."
            )
        if self._client is None:
            from typesafe_sdk import TypeSafeClient
            try:
                self._client = TypeSafeClient()
            except Exception as e:
                raise RuntimeError(f"Failed to initialize TypeSafe client: {e}")
        return self._client

    # ---- screen capture -------------------------------------------------

    def _current_app(self) -> dict:
        try:
            info = self.mobile.device.app_current()
            return {"package": info.get("package", ""), "activity": info.get("activity", "")}
        except Exception:
            return {"package": "", "activity": ""}

    def _elements(self) -> list:
        state = self.mobile.get_state(use_vision=False)
        return state.tree_state.interactive_elements

    @staticmethod
    def _element_key(el: "ElementNode"):
        return (el.resource_id or "", el.name or "", el.class_name or "")

    def mark_typed(self, el: "ElementNode") -> None:
        self._typed.add(self._element_key(el))

    @staticmethod
    def _short_class(class_name: str) -> str:
        return class_name.rsplit(".", 1)[-1] if class_name else ""

    def _element_options(self, elements: list) -> dict:
        """Choice criteria: option label -> description, plus a no-match outcome."""
        options = {NO_MATCH: "No element matches"}
        for i, el in enumerate(elements):
            parts = [el.name or "(no label)"]
            if el.resource_id:
                parts.append(f"id={el.resource_id}")
            parts.append(self._short_class(el.class_name))
            parts.append(f"at ({el.coordinates.x},{el.coordinates.y})")
            if el.editable:
                parts.append("editable")
            if el.focused:
                parts.append("focused")
            options[str(i)] = " ".join(parts)
        return options

    def _screen_state(self, elements: list, text_to_type: Optional[str] = None,
                      extra: Optional[dict] = None) -> dict:
        app = self._current_app()
        if app["package"] != self._typed_pkg:
            self._typed.clear()
            self._typed_pkg = app["package"]

        shown = elements[: self._max_elements]
        screen = []
        for i, el in enumerate(shown):
            screen.append({
                "index": i,
                "name": el.name or "",
                "id": el.resource_id or "",
                "type": self._short_class(el.class_name),
                "at": [el.coordinates.x, el.coordinates.y],
                "editable": el.editable,
                "focused": el.focused,
                "typed": self._element_key(el) in self._typed,
            })

        state = {"current_app": app, "screen": screen}
        if len(elements) > len(shown):
            state["screen_truncated"] = (
                f"showing first {len(shown)} of {len(elements)} interactive elements"
            )
        if text_to_type is not None:
            state["text_to_type"] = text_to_type
        if extra:
            state.update(extra)
        return state

    @staticmethod
    def _top_options(probabilities: dict, limit: int = 3) -> list:
        ranked = sorted(probabilities.items(), key=lambda kv: kv[1], reverse=True)
        return [
            {"option": option, "probability": round(p, 3)}
            for option, p in ranked[:limit]
        ]

    @staticmethod
    def _usage(result) -> dict:
        usage = getattr(result, "usage", None)
        if usage is None:
            return {}
        return {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}

    @staticmethod
    def _index_of(choice: Optional[str], count: int) -> Optional[int]:
        if choice is None or choice == NO_MATCH:
            return None
        try:
            idx = int(choice)
        except (TypeError, ValueError):
            return None
        return idx if 0 <= idx < count else None

    # ---- judgments ------------------------------------------------------

    def pick_element(self, description: str) -> dict:
        """Decide which interactive element best matches a natural-language target."""
        from typesafe_sdk import Choice, Noul

        all_elements = self._elements()
        if not all_elements:
            return {"found": False, "reason": "no_elements", "element_count": 0,
                    "element_index": None, "element": None, "presence": 0.0,
                    "confidence": 0.0, "alternatives": [], "usage": {},
                    "target": description}

        elements = all_elements[: self._max_elements]
        state = self._screen_state(elements, extra={"target": description})
        questions = {
            "match_present": Noul(
                instructions="Does `screen` contain an element matching the target "
                             "described in `target`?"
            ),
            "element": Choice(
                instructions="Which element in `screen` best matches the target "
                             "described in `target`? Answer with that element's index.",
                criteria=self._element_options(elements),
            ),
        }
        res = self.client.system_one(state, questions)

        presence = res.nouls["match_present"].noul
        ans = res.choices["element"]
        idx = self._index_of(ans.choice, len(elements))
        found = presence >= PRESENCE_THRESHOLD and idx is not None
        return {
            "found": found,
            "element_index": idx,
            "element": elements[idx] if idx is not None else None,
            "presence": round(presence, 3),
            "confidence": round(ans.confidence, 3),
            "alternatives": self._top_options(
                {k: v for k, v in ans.probabilities.items() if k != NO_MATCH}
            ),
            "element_count": len(all_elements),
            "target": description,
            "usage": self._usage(res),
        }

    def decide_step(self, goal: str, context: Optional[str] = None,
                    text_to_type: Optional[str] = None,
                    recent_actions: Optional[list] = None) -> dict:
        """Decide the next action that progresses `goal` from the current screen.

        Asks all questions in one request: whether the goal is done, which action
        to take, and speculatively which element each branch would need.
        """
        from typesafe_sdk import Choice, Noul

        all_elements = self._elements()
        if not all_elements:
            return {"action": "give_up", "reason": "no_elements",
                    "element_count": 0, "goal": goal}

        elements = all_elements[: self._max_elements]
        extra = {"goal": goal}
        if context:
            extra["context"] = context
        if recent_actions:
            extra["recent_actions"] = recent_actions[-5:]
        state = self._screen_state(elements, text_to_type=text_to_type, extra=extra)

        actions = dict(ACTIONS)
        if text_to_type is not None:
            actions = {"tap_element": actions["tap_element"],
                       "type_text": TYPE_TEXT_ACTION,
                       **{k: v for k, v in actions.items() if k != "tap_element"}}

        element_options = self._element_options(elements)
        questions = {
            "goal_achieved": Noul(
                instructions="Is `goal` already achieved or satisfied given what "
                             "`screen` shows?"
            ),
            "action": Choice(
                instructions=(
                    "What is the single best next action to progress `goal`? "
                    "When `text_to_type` is provided: choose type_text once the "
                    "right field is identified; once an element has typed=true "
                    "the text is entered, so choose press_enter or the next "
                    "needed action instead of typing again. Do not keep tapping "
                    "an element that is already focused. When `recent_actions` "
                    "shows a repeated action that did not change the screen, "
                    "choose a different action."
                ),
                criteria=actions,
            ),
            "tap_target": Choice(
                instructions="If the next action is to tap an element, which "
                             "element index in `screen` should be tapped to "
                             "progress `goal`?",
                criteria=element_options,
            ),
        }
        if text_to_type is not None:
            questions["type_target"] = Choice(
                instructions="If the next action is to type `text_to_type`, which "
                             "element index in `screen` should be tapped so the "
                             "correct text field gains focus? Prefer the field "
                             "meant for this goal (e.g. the page's own search box "
                             "over the browser URL bar); elements with "
                             "editable=true accept text directly.",
                criteria=element_options,
            )
        else:
            questions["needs_text"] = Noul(
                instructions="Does progressing `goal` from this screen require "
                             "entering text into a field?"
            )

        res = self.client.system_one(state, questions)

        goal_p = res.nouls["goal_achieved"].noul
        action_ans = res.choices["action"]
        action = action_ans.choice
        if goal_p >= GOAL_DONE_THRESHOLD and action not in ("give_up",):
            action = "done"

        idx = None
        if action == "tap_element":
            idx = self._index_of(res.choices["tap_target"].choice, len(elements))
        elif action == "type_text" and "type_target" in res.choices:
            idx = self._index_of(res.choices["type_target"].choice, len(elements))

        return {
            "action": action,
            "element_index": idx,
            "element": elements[idx] if idx is not None else None,
            "screen_key": hash(tuple(
                self._element_key(el) for el in elements
            )),
            "goal_achieved": round(goal_p, 3),
            "confidence": round(action_ans.confidence, 3),
            "needs_text": (
                round(res.nouls["needs_text"].noul, 3)
                if "needs_text" in res.nouls else None
            ),
            "alternatives": self._top_options(action_ans.probabilities),
            "element_count": len(all_elements),
            "goal": goal,
            "usage": self._usage(res),
        }

    def judge(self, question: str) -> dict:
        """Answer a yes/no question about the current screen."""
        from typesafe_sdk import Noul

        elements = self._elements()[: self._max_elements]
        state = self._screen_state(elements, extra={"question": question})
        res = self.client.system_one(state, {
            "verdict": Noul(
                instructions="Answer yes or no based on `screen`: `question`"
            )
        })
        p = res.nouls["verdict"].noul
        verdict = "yes" if p >= VERDICT_YES else "no" if p <= VERDICT_NO else "uncertain"
        return {"verdict": verdict, "probability": round(p, 3),
                "question": question, "usage": self._usage(res)}
