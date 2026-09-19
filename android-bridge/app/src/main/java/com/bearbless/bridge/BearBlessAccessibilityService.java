package com.bearbless.bridge;

import android.accessibilityservice.AccessibilityService;
import android.os.Bundle;
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
        Set<AccessibilityNodeInfo> clickable = new LinkedHashSet<>();
        for (AccessibilityNodeInfo match : matches) {
            AccessibilityNodeInfo target = match;
            while (target != null && !target.isClickable()) target = target.getParent();
            if (target != null && target.isEnabled()) clickable.add(target);
        }
        if (clickable.isEmpty() || (!allowMultiple && clickable.size() != 1)) {
            return "expected one clickable exact-text node, found " + clickable.size();
        }
        AccessibilityNodeInfo target = null;
        int bottom = Integer.MIN_VALUE;
        for (AccessibilityNodeInfo candidate : clickable) {
            Rect bounds = new Rect();
            candidate.getBoundsInScreen(bounds);
            // QQ repeats the same person in “最近转发” and “最近聊天”.
            // When the caller explicitly permits duplicate exact labels,
            // prefer the lower node: the stable full-width recent-chat row.
            if (bounds.bottom > bottom) {
                target = candidate;
                bottom = bounds.bottom;
            }
        }
        if (target == null) return "no clickable exact-text node";
        boolean ok = target.performAction(AccessibilityNodeInfo.ACTION_CLICK);
        return ok ? null : "ACTION_CLICK rejected by exact-text node";
    }

    private String setTextOnDisplay(int displayId, CharSequence text, boolean preferBottom) {
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
