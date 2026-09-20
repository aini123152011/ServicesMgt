import { createFileRoute, getRouteApi } from '@tanstack/react-router'
import { ServiceDetail } from '@/features/services/components/service-detail'

const routeApi = getRouteApi('/_authenticated/services/$serviceName')

// eslint-disable-next-line react-refresh/only-export-components
function ServiceDetailRoute() {
  const { serviceName } = routeApi.useParams()
  // key 挂参数：切换服务时重建表单，避免残留上一个服务的编辑值
  return <ServiceDetail key={serviceName} name={serviceName} />
}

export const Route = createFileRoute('/_authenticated/services/$serviceName')({
  component: ServiceDetailRoute,
})
