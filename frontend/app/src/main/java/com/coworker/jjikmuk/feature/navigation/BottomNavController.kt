package com.coworker.jjikmuk.feature.navigation

import android.content.Context
import android.view.View
import android.widget.Toast
import androidx.fragment.app.FragmentManager
import com.coworker.jjikmuk.R
import com.coworker.jjikmuk.feature.history.HistoryFragment
import com.coworker.jjikmuk.feature.home.HomeFragment
import com.coworker.jjikmuk.feature.scanner.BarcodeScannerActivity

object BottomNavController {

    fun bind(
        rootView: View,
        fragmentManager: FragmentManager,
        context: Context
    ) {
        rootView.findViewById<View?>(R.id.navHome)?.setOnClickListener {
            fragmentManager.popBackStack(null, FragmentManager.POP_BACK_STACK_INCLUSIVE)
            fragmentManager.beginTransaction()
                .replace(R.id.mainContainer, HomeFragment())
                .commit()
        }

        rootView.findViewById<View?>(R.id.navDiet)?.setOnClickListener {
            showComingSoon(context)
        }

        rootView.findViewById<View?>(R.id.navCamera)?.setOnClickListener {
            context.startActivity(
                android.content.Intent(context, BarcodeScannerActivity::class.java)
            )
        }

        rootView.findViewById<View?>(R.id.navHistory)?.setOnClickListener {
            fragmentManager.beginTransaction()
                .replace(R.id.mainContainer, HistoryFragment())
                .addToBackStack(null)
                .commit()
        }

        rootView.findViewById<View?>(R.id.navMy)?.setOnClickListener {
            showComingSoon(context)
        }
    }

    private fun showComingSoon(context: Context) {
        Toast.makeText(context, "향후 구현 예정입니다", Toast.LENGTH_SHORT).show()
    }
}
