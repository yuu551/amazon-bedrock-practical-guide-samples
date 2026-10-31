import { CognitoJwtVerifier } from "aws-jwt-verify"
import type { VerifiedUser } from "./app.js"

// Cognitoのアクセストークンを検証する。公開鍵はプロセス内にキャッシュされる
export function createTokenVerifier(options: {
  userPoolId: string
  clientId: string
}): (token: string) => Promise<VerifiedUser> {
  const verifier = CognitoJwtVerifier.create({
    userPoolId: options.userPoolId,
    clientId: options.clientId,
    tokenUse: "access",
  })

  return async (token) => {
    const payload = await verifier.verify(token)
    return { sub: payload.sub }
  }
}
