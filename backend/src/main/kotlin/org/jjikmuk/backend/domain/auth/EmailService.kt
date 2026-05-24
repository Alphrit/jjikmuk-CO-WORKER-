package org.jjikmuk.backend.domain.auth

import org.springframework.mail.SimpleMailMessage
import org.springframework.mail.javamail.JavaMailSender
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import java.time.LocalDateTime
import kotlin.random.Random

@Service
class EmailService(
    private val mailSender: JavaMailSender,
    private val emailVerificationRepository: EmailVerificationRepository
) {
    @Transactional
    fun sendVerificationCode(email: String) {
        // 1. 6자리 난수(인증번호) 생성
        val code = Random.nextInt(100000, 999999).toString()

        // 2. DB에 인증번호 저장 (이미 있다면 덮어쓰기)
        val expiredAt = LocalDateTime.now().plusMinutes(5) // 5분 유효
        var verification = emailVerificationRepository.findByEmail(email)

        if (verification != null) {
            verification.code = code
            verification.expiredAt = expiredAt
        } else {
            verification = EmailVerification(email = email, code = code, expiredAt = expiredAt)
        }
        emailVerificationRepository.save(verification)

        // 3. 실제 이메일 발송
        val message = SimpleMailMessage()
        message.setTo(email)
        message.subject = "[찍먹] 회원가입 이메일 인증번호입니다."
        message.text = "안녕하세요!\n요청하신 인증번호는 [$code] 입니다.\n5분 안에 입력해주세요."

        mailSender.send(message)
    }
    // 🚀 임시 비밀번호 메일 발송 함수
    fun sendTemporaryPassword(email: String, tempPassword: String) {
        val message = SimpleMailMessage()
        message.setTo(email)
        message.subject = "[찍먹] 임시 비밀번호가 발급되었습니다."
        message.text = """
            안녕하세요! 요청하신 임시 비밀번호를 보내드립니다.
            
            임시 비밀번호: [$tempPassword]
            
            보안을 위해 로그인 후 반드시 마이페이지에서 비밀번호를 변경해 주세요.
        """.trimIndent()

        mailSender.send(message)
    }
}