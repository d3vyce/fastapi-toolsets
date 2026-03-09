from fastapi_toolsets.crud import CrudFactory

from .models import OAuthAccount, OAuthProvider, Team, User, UserToken

TeamCrud = CrudFactory(model=Team)
UserCrud = CrudFactory(model=User)
UserTokenCrud = CrudFactory(model=UserToken)
OAuthProviderCrud = CrudFactory(model=OAuthProvider)
OAuthAccountCrud = CrudFactory(model=OAuthAccount)
