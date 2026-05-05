package org.jjikmuk.backend.domain.user

import jakarta.persistence.*

@Entity
@Table(name = "users")
class User(
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    val id: Long? = null,

    @Column(nullable = false, unique = true)
    val email: String,

    @Column(nullable = false)
    var nickname: String,

    @Column(length = 1000)
    var allergies: String? = null,

    @Column(length = 1000)
    var diseases: String? = null,

    var specialDiet: String? = null,
    var dislikedIngredients: String? = null
){
    fun updateProfile(nickname: String, allergies: String?, diseases: String?) {
        this.nickname = nickname
        this.allergies = allergies
        this.diseases = diseases
    }
}