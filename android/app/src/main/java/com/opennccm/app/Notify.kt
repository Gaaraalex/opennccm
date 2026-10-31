package com.opennccm.app

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/**
 * Avisos en el teléfono sin Google: cada 15 minutos (el mínimo de Android) la app pregunta al servidor qué alarmas
 * graves y cambios por aprobar hay desde el último aviso, y los muestra en la pantalla bloqueada.
 */
object Notify {
    const val CH_ALARM = "alarmas"
    const val CH_CHANGE = "cambios"
    private const val WORK = "opennccm-avisos"

    fun channels(ctx: Context) {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CH_ALARM, "Alarmas graves", NotificationManager.IMPORTANCE_HIGH))
            nm.createNotificationChannel(NotificationChannel(CH_CHANGE, "Cambios por aprobar", NotificationManager.IMPORTANCE_DEFAULT))
        }
    }

    fun schedule(ctx: Context, on: Boolean) {
        val wm = WorkManager.getInstance(ctx)
        if (!on) {
            wm.cancelUniqueWork(WORK)
            return
        }
        val req = PeriodicWorkRequestBuilder<PollWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        wm.enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP, req)
    }

    fun show(ctx: Context, id: Int, channel: String, title: String, text: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        val open = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(ctx, channel)
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setPriority(if (channel == CH_ALARM) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
            .build()
        runCatching { NotificationManagerCompat.from(ctx).notify(id, n) }
    }
}

class PollWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val store = Store(applicationContext)
        if (!store.connected || !store.notify) return Result.success()
        return try {
            val api = Api(store)
            val r = api.obj("/api/mobile/notifications?since=" + store.lastNotified)
            val items = r.optJSONArray("items")
            if (store.lastNotified > 0 && items != null) {
                for (i in 0 until items.length()) {
                    val it = items.getJSONObject(i)
                    val change = it.optString("kind") == "change"
                    Notify.show(applicationContext, it.optInt("id"), if (change) Notify.CH_CHANGE else Notify.CH_ALARM,
                        it.optString("title"), it.optString("text"))
                }
            }
            store.lastNotified = r.optLong("last", store.lastNotified)
            Result.success()
        } catch (e: Exception) {
            Result.retry()
        }
    }
}
