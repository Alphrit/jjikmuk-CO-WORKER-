package org.jjikmuk.backend.domain.product

import jakarta.persistence.Column
import jakarta.persistence.Entity
import jakarta.persistence.Id
import jakarta.persistence.Table

@Entity
@Table(name = "products")
class Product(
    @Id
    @Column(name = "barcode", nullable = false)
    val barcode: String, // 🚀 PK가 바코드로 변경됨!

    @Column(name = "product_name", length = 1000)
    val productName: String?,

    @Column(name = "manufacturer")
    val manufacturer: String?,

    @Column(name = "report_no")
    val reportNo: String?, // 이제 PK가 아닌 일반 컬럼

    @Column(name = "allergy", length = 1000)
    val allergy: String?,

    @Column(name = "nutrient_text", columnDefinition = "TEXT")
    val nutrientText: String?, // 영양성분 원본 텍스트

    @Column(name = "image_url", length = 2000)
    val imageUrl: String?,

    @Column(name = "source")
    val source: String?, // 출처 (추가됨)

    @Column(name = "raw_materials", columnDefinition = "TEXT")
    val rawMaterials: String?,

    @Column(name = "energy_kcal")
    val energyKcal: Double?,

    @Column(name = "carbs_g")
    val carbsG: Double?,

    @Column(name = "protein_g")
    val proteinG: Double?,

    @Column(name = "fat_g")
    val fatG: Double?,

    @Column(name = "sugar_g")
    val sugarG: Double?,

    @Column(name = "sodium_mg")
    val sodiumMg: Double?,

    @Column(name = "cholesterol_mg")
    val cholesterolMg: Double?,

    @Column(name = "allergy_warning", length = 1000)
    val allergyWarning: String?, // 기원 불명 경고 성분

    @Column(name = "clean_product_name", length = 1000)
    val cleanProductName: String?,

    @Column(name = "total_weight")
    val totalWeight: String?,

    @Column(name = "carbs_percent")
    val carbsPercent: Double?,

    @Column(name = "protein_percent")
    val proteinPercent: Double?,

    @Column(name = "fat_percent")
    val fatPercent: Double?,

    @Column(name = "sodium_g")
    val sodiumG: Double?,

    @Column(name = "cholesterol_g")
    val cholesterolG: Double?

)