package com.bearbless.bridge;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
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
        if ("request_send_confirmation".equals(method) && arg != null) {
            return requestSendConfirmation(arg);
        }
        if ("send_confirmation_status".equals(method) && arg != null) {
            String status = getContext().getSharedPreferences("confirmations", Context.MODE_PRIVATE)
                    .getString(arg, "pending");
            reply.putBoolean("ok", true);
            reply.putString("status", status);
            return reply;
        }
        if ("health".equals(method)) {
            reply.putBoolean("enabled", service != null);
            return reply;
        }
        if ("click_text_exact".equals(method) && arg != null && service != null) {
            try {
                String[] fields = arg.split(":", 4);
                int displayId = Integer.parseInt(fields[0]);
                String packageName = decode(fields[1]);
                String expectedText = decode(fields[2]);
                boolean allowMultiple = fields.length == 4 && "1".equals(fields[3]);
                String error = service.clickExactTextOnDisplay(displayId, packageName, expectedText, allowMultiple);
                reply.putBoolean("ok", error == null);
                if (error != null) reply.putString("error", error);
            } catch (RuntimeException error) {
                reply.putBoolean("ok", false);
                reply.putString("error", "semantic click failed: " + error.getClass().getSimpleName()
                        + ": " + String.valueOf(error.getMessage()));
            }
            return reply;
        }
        if ("find_text_exact_center".equals(method) && arg != null && service != null) {
            try {
                String[] fields = arg.split(":", 5);
                int displayId = Integer.parseInt(fields[0]);
                String packageName = decode(fields[1]);
                String expectedText = decode(fields[2]);
                boolean allowMultiple = fields.length >= 4 && "1".equals(fields[3]);
                boolean preferBottom = fields.length == 5 && "bottom".equals(fields[4]);
                String center = service.findExactTextCenterOnDisplay(
                        displayId, packageName, expectedText, allowMultiple, preferBottom);
                boolean ok = center != null && !center.startsWith("error:");
                reply.putBoolean("ok", ok);
                if (ok) reply.putString("center", center);
                else reply.putString("error", center == null ? "exact-text lookup failed" : center);
            } catch (RuntimeException error) {
                reply.putBoolean("ok", false);
                reply.putString("error", "exact-text lookup failed: " + error.getClass().getSimpleName());
            }
            return reply;
        }
        boolean setText = "set_text".equals(method);
        boolean setBottomText = "set_text_bottom".equals(method);
        boolean setBottomTextSilent = "set_text_bottom_silent".equals(method);
        if ((!setText && !setBottomText && !setBottomTextSilent) || arg == null || service == null) {
            reply.putBoolean("ok", false);
            reply.putString("error", service == null ? "accessibility service is disabled" : "invalid request");
            return reply;
        }
        try {
            int split = arg.indexOf(':');
            int displayId = Integer.parseInt(arg.substring(0, split));
            byte[] raw = Base64.decode(arg.substring(split + 1), Base64.URL_SAFE | Base64.NO_PADDING);
            String decoded = new String(raw, StandardCharsets.UTF_8);
            String error = setBottomTextSilent
                    ? service.setTextOnBottomEditableSilently(displayId, decoded)
                    : setBottomText
                            ? service.setTextOnBottomEditable(displayId, decoded)
                            : service.setTextOnDisplay(displayId, decoded);
            reply.putBoolean("ok", error == null);
            if (error != null) reply.putString("error", error);
        } catch (RuntimeException error) {
            reply.putBoolean("ok", false);
            reply.putString("error", "invalid payload: " + error.getClass().getSimpleName());
        }
        return reply;
    }

    private static String decode(String value) {
        return new String(Base64.decode(value, Base64.URL_SAFE | Base64.NO_PADDING), StandardCharsets.UTF_8);
    }

    private Bundle requestSendConfirmation(String arg) {
        Bundle reply = new Bundle();
        try {
            String[] fields = arg.split(":", 3);
            String token = fields[0];
            String recipient = new String(Base64.decode(fields[1], Base64.URL_SAFE | Base64.NO_PADDING), StandardCharsets.UTF_8);
            String message = new String(Base64.decode(fields[2], Base64.URL_SAFE | Base64.NO_PADDING), StandardCharsets.UTF_8);
            Context context = getContext();
            NotificationManager manager = context.getSystemService(NotificationManager.class);
            String channelId = "bearbless_confirmations";
            manager.createNotificationChannel(new NotificationChannel(
                    channelId, "BearBless 操作确认", NotificationManager.IMPORTANCE_HIGH));
            int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
            Intent approve = new Intent(context, ConfirmationReceiver.class)
                    .setAction(ConfirmationReceiver.ACTION_APPROVE)
                    .putExtra(ConfirmationReceiver.EXTRA_TOKEN, token);
            Intent cancel = new Intent(context, ConfirmationReceiver.class)
                    .setAction(ConfirmationReceiver.ACTION_CANCEL)
                    .putExtra(ConfirmationReceiver.EXTRA_TOKEN, token);
            Notification notification = new Notification.Builder(context, channelId)
                    .setSmallIcon(android.R.drawable.ic_dialog_info)
                    .setContentTitle("确认给“" + recipient + "”发送消息？")
                    .setContentText(message)
                    .setStyle(new Notification.BigTextStyle().bigText(message))
                    .setAutoCancel(false)
                    .setOngoing(true)
                    .addAction(new Notification.Action.Builder(null, "取消",
                            PendingIntent.getBroadcast(context, token.hashCode() + 1, cancel, flags)).build())
                    .addAction(new Notification.Action.Builder(null, "发送",
                            PendingIntent.getBroadcast(context, token.hashCode() + 2, approve, flags)).build())
                    .build();
            context.getSharedPreferences("confirmations", Context.MODE_PRIVATE)
                    .edit().putString(token, "pending").apply();
            manager.notify(token, token.hashCode(), notification);
            reply.putBoolean("ok", true);
        } catch (RuntimeException error) {
            reply.putBoolean("ok", false);
            reply.putString("error", error.getClass().getSimpleName());
        }
        return reply;
    }

    @Override public String getType(Uri uri) { return null; }
    @Override public Cursor query(Uri u, String[] p, String s, String[] a, String o) { return null; }
    @Override public Uri insert(Uri uri, ContentValues values) { return null; }
    @Override public int delete(Uri uri, String selection, String[] args) { return 0; }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { return 0; }
}
