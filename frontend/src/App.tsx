import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Interfaces from './pages/Interfaces'
import Peers from './pages/Peers'
import Nodes from './pages/Nodes'
import Routing from './pages/Routing'
import GeoIP from './pages/GeoIP'
import Backup from './pages/Backup'
import DNS from './pages/DNS'
import Settings from './pages/Settings'

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 10_000,
    },
  },
})

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('token')
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/*"
            element={
              <RequireAuth>
                <Layout>
                  <Routes>
                    <Route path="/" element={<Dashboard />} />
                    <Route path="/interfaces" element={<Interfaces />} />
                    <Route path="/peers" element={<Peers />} />
                    <Route path="/nodes" element={<Nodes />} />
                    <Route path="/routing" element={<Routing />} />
                    <Route path="/geoip" element={<GeoIP />} />
                    <Route path="/dns" element={<DNS />} />
                    <Route path="/backup" element={<Backup />} />
                    <Route path="/settings" element={<Settings />} />
                    <Route path="*" element={<Navigate to="/" replace />} />
                  </Routes>
                </Layout>
              </RequireAuth>
            }
          />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
