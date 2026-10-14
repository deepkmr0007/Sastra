package com.sastra.app.work

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.sastra.app.MainActivity
import com.sastra.app.R
import com.sastra.app.SastraApp
import com.sastra.app.data.objects

/**
 * Background sync (every ~15 min when online): pushes the offline outbox and
 * turns new server notifications (task finished/failed, approval needed,
 * security alert) into phone notifications.
 */
class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        val app = applicationContext as SastraApp
        val repo = app.repo
        if (!repo.loggedIn) return Result.success()
        return try {
            repo.sync()
            val since = repo.store.getString("notif_since", "0")
            val list = repo.get("/api/notifications?unread=1&since=$since").optJSONArray("notifications")?.objects().orEmpty()
            var newest = since.toDoubleOrNull() ?: 0.0
            for (n in list.reversed()) {
                newest = maxOf(newest, n.optDouble("ts", 0.0))
                val title = n.optString("title")
                val channel = when {
                    title.contains("Approval", true) -> SastraApp.CH_APPROVALS
                    n.optString("kind") == "security" -> SastraApp.CH_SECURITY
                    else -> SastraApp.CH_TASKS
                }
                notify(n.optString("id").hashCode(), channel, title, n.optString("body"))
            }
            repo.store.putString("notif_since", newest.toString())
            val status = repo.get("/api/status")
            if (status.optInt("security_alerts", 0) > 0 && repo.store.getString("last_alert_count", "0") != status.optInt("security_alerts").toString()) {
                notify(9001, SastraApp.CH_SECURITY, "Security alert", "${status.optInt("security_alerts")} unacknowledged — open Security")
                repo.store.putString("last_alert_count", status.optInt("security_alerts").toString())
            }
            Result.success()
        } catch (e: Exception) {
            Result.retry()
        }
    }

    private fun notify(id: Int, channel: String, title: String, body: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(applicationContext, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val intent = PendingIntent.getActivity(applicationContext, 0, Intent(applicationContext, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = NotificationCompat.Builder(applicationContext, channel)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(title)
            .setContentText(body.take(200))
            .setStyle(NotificationCompat.BigTextStyle().bigText(body.take(1000)))
            .setContentIntent(intent)
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(applicationContext).notify(id, n)
        } catch (_: SecurityException) {
        }
    }
}
