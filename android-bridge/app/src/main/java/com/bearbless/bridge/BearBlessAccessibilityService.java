package com.bearbless.bridge;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.graphics.Path;
import android.graphics.Rect;
import android.util.SparseArray;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

public final class BearBlessAccessibilityService extends AccessibilityService {
    private static volatile BearBlessAccessibilityService instance;

    static BearBlessAccessibilityService getInstance() { return instance; }

    @Override public void onServiceConnected() { instance = this; }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) { }
    @Override public void onInterrupt() { }
    @Override public void onDestroy() { instance = null; super.onDestroy(); }

    synchronized String setTextOnDisplay(int displayId, CharSequence text) {
        return setTextOnDisplay(displayId, text, false);
    }

    synchronized String setTextOnBottomEditable(int displayId, CharSequence text) {
        return setTextOnDisplay(displayId, text, true);
    }

    synchronized String setTextOnBottomEditableSilently(int displayId, CharSequence text) {
        SoftKeyboardController keyboard = getSoftKeyboardController();
        int previousMode = keyboard.getShowMode();
        if (!keyboard.setShowMode(SHOW_MODE_HIDDEN)) {
            return "could not suppress the soft keyboard";
        }
        String error = setTextOnDisplay(displayId, text, true, true);
        new Handler(Looper.getMainLooper()).postDelayed(
                () -> keyboard.setShowMode(previousMode), 350L);
        return error;
    }

    synchronized String clickExactTextOnDisplay(
            int displayId, String packageName, String expectedText, boolean allowMultiple) {
        if (displayId <= 0) return "display 0 is forbidden";
        if (packageName == null || packageName.isEmpty()) return "package is required";
        if (expectedText == null || expectedText.trim().isEmpty()) return "text is required";
        SparseArray<List<AccessibilityWindowInfo>> all = getWindowsOnAllDisplays();
        List<AccessibilityWindowInfo> windows = all.get(displayId);
        if (windows == null || windows.isEmpty()) return "no accessibility windows for display " + displayId;

        List<AccessibilityNodeInfo> matches = new ArrayList<>();
        for (AccessibilityWindowInfo window : windows) {
            if (window.getDisplayId() != displayId) continue;
            AccessibilityNodeInfo root = window.getRoot();
            if (root != null) collectExactTextTargets(root, packageName, normalize(expectedText), matches);
        }
        if (matches.isEmpty() || (!allowMultiple && matches.size() != 1)) {
            return "expected one exact-text node, found " + matches.size();
        }
        // Pick the visual text occurrence before walking to a clickable
        // ancestor. QQ can expose both labels through one common clickable
        // container; de-duplicating ancestors first loses which occurrence
        // was the lower recent-chat row and may click the account header.
        AccessibilityNodeInfo selectedMatch = null;
        int top = Integer.MAX_VALUE;
        for (AccessibilityNodeInfo candidate : matches) {
            Rect bounds = new Rect();
            candidate.getBoundsInScreen(bounds);
            // QQ repeats the same person in “最近转发” and “最近聊天”.
            // On the share surface only the upper “最近转发” avatar is a
            // responsive share target; the lower recent-chat label may expose
            // Accessibility text while its row ignores both ACTION_CLICK and
            // display-scoped gestures. Prefer the top-most exact node.
            if (bounds.top < top) {
                selectedMatch = candidate;
                top = bounds.top;
            }
        }
        if (selectedMatch == null) return "no exact-text node selected";
        Rect selectedBounds = new Rect();
        selectedMatch.getBoundsInScreen(selectedBounds);
        if (selectedBounds.isEmpty()) return "exact-text node has empty bounds";

        // QQ's custom share-list rows report ACTION_CLICK as accepted while
        // doing nothing. Preserve the exact Accessibility identity match, but
        // dispatch a real tap at that node's visual centre on the same virtual
        // display. GestureDescription#setDisplayId keeps the gesture away from
        // Display 0 and avoids any model-guessed coordinates.
        Path path = new Path();
        path.moveTo(selectedBounds.exactCenterX(), selectedBounds.exactCenterY());
        GestureDescription gesture = new GestureDescription.Builder()
                .setDisplayId(displayId)
                .addStroke(new GestureDescription.StrokeDescription(path, 0L, 60L))
                .build();
        boolean queued = dispatchGesture(gesture, null, null);
        return queued ? null : "display-scoped exact-text gesture was rejected";
    }

    synchronized String findExactTextCenterOnDisplay(
            int displayId, String packageName, String expectedText,
            boolean allowMultiple, boolean preferBottom) {
        if (displayId <= 0) return "error:display 0 is forbidden";
        if (packageName == null || packageName.isEmpty()) return "error:package is required";
        if (expectedText == null || expectedText.trim().isEmpty()) return "error:text is required";
        SparseArray<List<AccessibilityWindowInfo>> all = getWindowsOnAllDisplays();
        List<AccessibilityWindowInfo> windows = all.get(displayId);
        if (windows == null || windows.isEmpty()) {
            return "error:no accessibility windows for display " + displayId;
        }
        List<AccessibilityNodeInfo> matches = new ArrayList<>();
        for (AccessibilityWindowInfo window : windows) {
            if (window.getDisplayId() != displayId) continue;
            AccessibilityNodeInfo root = window.getRoot();
            if (root != null) collectExactTextTargets(root, packageName, normalize(expectedText), matches);
        }
        if (matches.isEmpty() || (!allowMultiple && matches.size() != 1)) {
            return "error:expected one exact-text node, found " + matches.size();
        }
        AccessibilityNodeInfo selected = null;
        Rect selectedBounds = new Rect();
        int edge = preferBottom ? Integer.MIN_VALUE : Integer.MAX_VALUE;
        for (AccessibilityNodeInfo candidate : matches) {
            Rect bounds = new Rect();
            candidate.getBoundsInScreen(bounds);
            boolean preferred = preferBottom ? bounds.bottom > edge : bounds.top < edge;
            if (!bounds.isEmpty() && preferred) {
                selected = candidate;
                selectedBounds.set(bounds);
                edge = preferBottom ? bounds.bottom : bounds.top;
            }
        }
        if (selected == null) return "error:no bounded exact-text node";
        return selectedBounds.centerX() + "," + selectedBounds.centerY();
    }

    private String setTextOnDisplay(int displayId, CharSequence text, boolean preferBottom) {
        return setTextOnDisplay(displayId, text, preferBottom, false);
    }

    private String setTextOnDisplay(
            int displayId, CharSequence text, boolean preferBottom, boolean clearFocus) {
        if (displayId <= 0) return "display 0 is forbidden";
        SparseArray<List<AccessibilityWindowInfo>> all = getWindowsOnAllDisplays();
        List<AccessibilityWindowInfo> windows = all.get(displayId);
        if (windows == null || windows.isEmpty()) return "no accessibility windows for display " + displayId;

        List<AccessibilityNodeInfo> candidates = new ArrayList<>();
        for (AccessibilityWindowInfo window : windows) {
            if (window.getDisplayId() != displayId) continue;
            AccessibilityNodeInfo root = window.getRoot();
            if (root != null) {
                AccessibilityNodeInfo focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
                if (focused != null && supportsSetText(focused)) candidates.add(focused);
                collectTextTargets(root, candidates, focused);
            }
        }
        if (candidates.isEmpty()) return "no editable node on display " + displayId;

        AccessibilityNodeInfo target = null;
        if (preferBottom) {
            int lowestBottom = -1;
            for (AccessibilityNodeInfo node : candidates) {
                Rect bounds = new Rect();
                node.getBoundsInScreen(bounds);
                if (bounds.bottom > lowestBottom) {
                    lowestBottom = bounds.bottom;
                    target = node;
                }
            }
            if (target == null || lowestBottom < 1200) {
                return "no bottom editable node on display " + displayId;
            }
        }
        for (AccessibilityNodeInfo node : candidates) {
            if (preferBottom) break;
            if (node.isFocused()) {
                if (target != null) return "multiple focused editable nodes";
                target = node;
            }
        }
        if (target == null) {
            if (candidates.size() != 1) return "ambiguous editable nodes: " + candidates.size();
            target = candidates.get(0);
        }

        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        boolean ok = target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        if (ok && clearFocus) target.performAction(AccessibilityNodeInfo.ACTION_CLEAR_FOCUS);
        return ok ? null : "ACTION_SET_TEXT rejected by target node";
    }

    private static boolean supportsSetText(AccessibilityNodeInfo node) {
        return node.isEnabled()
                && node.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_TEXT);
    }

    private static void collectTextTargets(
            AccessibilityNodeInfo node,
            List<AccessibilityNodeInfo> out,
            AccessibilityNodeInfo focused) {
        if (supportsSetText(node) && !node.equals(focused)) {
            out.add(node);
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) collectTextTargets(child, out, focused);
        }
    }

    private static void collectExactTextTargets(
            AccessibilityNodeInfo node,
            String packageName,
            String expected,
            List<AccessibilityNodeInfo> out) {
        CharSequence nodePackage = node.getPackageName();
        if (nodePackage != null && packageName.contentEquals(nodePackage)) {
            String text = node.getText() == null ? "" : normalize(node.getText().toString());
            String description = node.getContentDescription() == null
                    ? "" : normalize(node.getContentDescription().toString());
            if (expected.equals(text) || expected.equals(description)) out.add(node);
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child != null) collectExactTextTargets(child, packageName, expected, out);
        }
    }

    private static String normalize(String value) {
        return value.replaceAll("\\s+", "").trim();
    }
}
