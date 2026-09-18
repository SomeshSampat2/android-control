"""Live Jev test on the emulator: judge -> pick_element -> decide_step loop."""
import time
from android_mcp.mobile.service import Mobile
from android_mcp.jev.service import Jev


def show_screen(jev, label):
    els = jev._elements()
    print(f"\n--- {label}: {len(els)} interactive elements ---")
    for i, e in enumerate(els[:12]):
        print(f"  {i}: {e.name!r} {e.class_name.split('.')[-1]} at ({e.coordinates.x},{e.coordinates.y})")


mobile = Mobile()
for i in range(6):
    try:
        mobile.connect("emulator-5554")
        break
    except Exception as e:
        print(f"connect attempt {i + 1} failed, retrying…")
        time.sleep(3)
jev = Jev(mobile)
print("status:", jev.status)

# 1) judge: yes/no question about the screen
t = time.time()
verdict = jev.judge("Is this the Android home screen or app drawer?")
print(f"\n[1] JevCheck 'home screen?' -> {verdict['verdict']} (p={verdict['probability']}) in {time.time()-t:.2f}s")

# 2) pick_element: natural language target -> element
t = time.time()
res = jev.pick_element("the Messages app icon")
dt = time.time() - t
print(f"\n[2] JevPick 'Messages app' -> found={res['found']} idx={res['element_index']} "
      f"presence={res['presence']} conf={res['confidence']} in {dt:.2f}s")
print("    alternatives:", res["alternatives"])
if res["found"]:
    el = res["element"]
    mobile.device.click(el.coordinates.x, el.coordinates.y)
    print(f"    -> tapped '{el.name}' at ({el.coordinates.x},{el.coordinates.y})")
    time.sleep(2.5)
    show_screen(jev, "after tap")

# 3) decide_step loop: goal-driven navigation
print("\n[3] JevRun goal='go back to the home screen'")
mobile.device.press("back")
time.sleep(1.5)
show_screen(jev, "current screen")

t = time.time()
step = jev.decide_step("open the YouTube app")
dt = time.time() - t
print(f"    decide_step -> action={step['action']} idx={step['element_index']} "
      f"goal_achieved={step['goal_achieved']} conf={step['confidence']} "
      f"needs_text={step['needs_text']} in {dt:.2f}s")
print("    alternatives:", step["alternatives"])
if step["action"] == "tap_element" and step["element"] is not None:
    el = step["element"]
    mobile.device.click(el.coordinates.x, el.coordinates.y)
    print(f"    -> tapped '{el.name}'")
    time.sleep(3)
    t = time.time()
    done = jev.judge("Is the YouTube app open?")
    print(f"    JevCheck 'YouTube open?' -> {done['verdict']} (p={done['probability']}) in {time.time()-t:.2f}s")
