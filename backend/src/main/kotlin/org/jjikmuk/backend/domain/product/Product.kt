package org.jjikmuk.backend.domain.product

import jakarta.persistence.Column
import jakarta.persistence.Entity
import jakarta.persistence.Id
import jakarta.persistence.Table

@Entity
@Table(name = "products")
class Product(
    @Id
    val reportNo: String,

    val barcode: String?,
    val foodCode: String?,

    @Column(length = 1000)
    val productName: String?,

    val manufacturer: String?,
    val prdlstDcnm: String?,

    @Column(length = 2000)
    val imageUrl: String?,

    @Column(length = 1000)
    val allergy: String?,

    @Column(columnDefinition = "TEXT")
    val rawmtrlNm: String?,
    val servingSize: String?,
    val calories: Double?,
    val carbs: Double?,
    val protein: Double?,
    val fat: Double?,
    val sugar: Double?,
    val sodium: Double?,
    val cholesterol: Double?,
    val saturatedFat: Double?,
    val transFat: Double?
)