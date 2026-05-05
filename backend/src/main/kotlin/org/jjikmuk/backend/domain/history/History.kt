package org.jjikmuk.backend.domain.history

import jakarta.persistence.*
import org.jjikmuk.backend.domain.user.User
import java.time.LocalDateTime

@Entity
@Table(name = "histories")
class History(
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    val id: Long? = null,

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    val user: User,

    @Column(nullable = false)
    val barcode: String, // 16.csv와 연결할 핵심 키

    @Column(nullable = false)
    val actionType: String, // "SCAN", "EAT" 등

    @Column(nullable = false)
    val createdAt: LocalDateTime = LocalDateTime.now()
)