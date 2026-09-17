package com.bearbless.bridge;

import android.accessibilityservice.AccessibilityService;
import android.os.Bundle;
import android.util.SparseArray;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import java.util.ArrayList;
import java.util.List;

public final class BearBlessAccessibilityService extends AccessibilityService {
    private static volatile BearBlessAccessibilityService instance;

    static BearBlessAccessibilityService getInstance() { return instance; }

    @Override public void onServiceConnected() { instance = this; }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) { }
    @Override public void onInterrupt() { }
    @Override public void onDestroy() { instance = null; super.onDestroy(); }

    synchronized String setTextOnDisplay(int displayId, CharSequence text) {
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
        for (AccessibilityNodeInfo node : candidates) {
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
}
