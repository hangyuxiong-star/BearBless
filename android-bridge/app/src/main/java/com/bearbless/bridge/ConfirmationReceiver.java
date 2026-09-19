package com.bearbless.bridge;

import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class ConfirmationReceiver extends BroadcastReceiver {
    static final String ACTION_APPROVE = "com.bearbless.bridge.APPROVE_SEND";
    static final String ACTION_CANCEL = "com.bearbless.bridge.CANCEL_SEND";
    static final String EXTRA_TOKEN = "token";

    @Override public void onReceive(Context context, Intent intent) {
        String token = intent.getStringExtra(EXTRA_TOKEN);
        if (token == null || token.isEmpty()) return;
        String status = ACTION_APPROVE.equals(intent.getAction()) ? "approved" : "cancelled";
        context.getSharedPreferences("confirmations", Context.MODE_PRIVATE)
                .edit().putString(token, status).apply();
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager != null) manager.cancel(token, token.hashCode());
    }
}
