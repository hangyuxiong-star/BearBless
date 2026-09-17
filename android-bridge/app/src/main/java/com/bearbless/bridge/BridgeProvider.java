package com.bearbless.bridge;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.util.Base64;

import java.nio.charset.StandardCharsets;

public final class BridgeProvider extends ContentProvider {
    private static final int ROOT_UID = 0;
    private static final int SHELL_UID = 2000;
    @Override public boolean onCreate() { return true; }

    @Override public Bundle call(String method, String arg, Bundle extras) {
        Bundle reply = new Bundle();
        int caller = Binder.getCallingUid();
        if (caller != SHELL_UID && caller != ROOT_UID) {
            reply.putBoolean("ok", false);
            reply.putString("error", "caller is not adb shell");
            return reply;
        }
        BearBlessAccessibilityService service = BearBlessAccessibilityService.getInstance();
        if ("health".equals(method)) {
            reply.putBoolean("enabled", service != null);
            return reply;
        }
        if (!"set_text".equals(method) || arg == null || service == null) {
            reply.putBoolean("ok", false);
            reply.putString("error", service == null ? "accessibility service is disabled" : "invalid request");
            return reply;
        }
        try {
            int split = arg.indexOf(':');
            int displayId = Integer.parseInt(arg.substring(0, split));
            byte[] raw = Base64.decode(arg.substring(split + 1), Base64.URL_SAFE | Base64.NO_PADDING);
            String error = service.setTextOnDisplay(displayId, new String(raw, StandardCharsets.UTF_8));
            reply.putBoolean("ok", error == null);
            if (error != null) reply.putString("error", error);
        } catch (RuntimeException error) {
            reply.putBoolean("ok", false);
            reply.putString("error", "invalid payload: " + error.getClass().getSimpleName());
        }
        return reply;
    }

    @Override public String getType(Uri uri) { return null; }
    @Override public Cursor query(Uri u, String[] p, String s, String[] a, String o) { return null; }
    @Override public Uri insert(Uri uri, ContentValues values) { return null; }
    @Override public int delete(Uri uri, String selection, String[] args) { return 0; }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { return 0; }
}
